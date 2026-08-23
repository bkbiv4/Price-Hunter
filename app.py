from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv, set_key

import database
import price_refresh
from business_ui import (
    render_allocation,
    render_expenses,
    render_grading,
    render_inventory_history,
    render_purchases,
    render_receipts,
    render_reports,
    render_sales,
)
from ebay import (
    EbayClient,
    EbayConfig,
    EbayError,
    ebay_draft_row,
    listing_payloads,
    listing_readiness_issues,
)
from listings import build_description, build_title, suggested_price
from scp_import import REQUIRED_COLUMNS, collection_row_to_card, collection_type, import_identity
from sportscardspro import (
    SportsCardsProClient,
    SportsCardsProError,
    available_prices,
    build_card_search_query,
    inventory_grade_prices,
    matches_parallel,
    matches_terms,
    product_price_row,
)
from tcgcollector_import import (
    REQUIRED_COLUMNS as TCGCOLLECTOR_REQUIRED_COLUMNS,
    reconcile_collection_rows,
    tcgcollector_row_to_card,
)


ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(ENV_PATH)
database.initialize()


def sync_market_value(guide_prices: dict[str, float]) -> None:
    """Keep market value aligned with the selected SportsCardsPro price."""
    selected_basis = st.session_state.get("add_price_basis")
    st.session_state.add_market_value = float(guide_prices.get(selected_basis, 0.0))


def render_backup_restore() -> None:
    st.subheader("Backup and restore")
    db_path = database.database_path()
    counts = database.table_counts()
    st.write(f"Active database: `{db_path}`")
    st.caption(
        "Inventory and business records live in this local SQLite database. "
        "Git ignores this file, so use backups when moving between computers."
    )
    metrics = st.columns(4)
    metrics[0].metric("Inventory rows", f"{counts.get('cards', 0):,}")
    metrics[1].metric("Sales", f"{counts.get('sales', 0):,}")
    metrics[2].metric("Purchases", f"{counts.get('purchases', 0):,}")
    metrics[3].metric("Receipts", f"{counts.get('receipts', 0):,}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_columns = st.columns(2)
    backup_columns[0].download_button(
        "Download full database backup",
        database.backup_bytes(),
        file_name=f"price_hunter-{timestamp}.db",
        mime="application/vnd.sqlite3",
        disabled=not database.database_exists(),
    )
    backup_columns[1].download_button(
        "Export all tables as CSV zip",
        database.export_csv_zip(),
        file_name=f"price_hunter-csv-{timestamp}.zip",
        mime="application/zip",
        disabled=not database.database_exists(),
    )

    with st.expander("Restore from database backup"):
        upload = st.file_uploader("SQLite database backup", type=["db", "sqlite", "sqlite3"])
        confirmation = st.text_input(
            "Type RESTORE to replace the active database",
            help="A safety copy of the current database is created before replacement.",
        )
        if st.button(
            "Restore uploaded database",
            disabled=not (upload and confirmation == "RESTORE"),
            type="primary",
        ):
            try:
                safety_backup = database.restore_database(upload.getvalue())
                if safety_backup:
                    st.success(f"Database restored. Previous database saved as `{safety_backup}`.")
                else:
                    st.success("Database restored.")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

st.set_page_config(page_title="Price Hunter", page_icon="🏷️", layout="wide")
st.title("Price Hunter")
st.caption("Organize sports cards and prepare consistent eBay listings.")

with st.sidebar:
    st.header("SportsCardsPro")
    token = st.text_input(
        "API token",
        value=os.getenv("SPORTSCARDSPRO_TOKEN", ""),
        type="password",
        help="Use Remember token to save it privately on this computer.",
    )
    if st.button("Remember token on this computer", disabled=not token):
        set_key(str(ENV_PATH), "SPORTSCARDSPRO_TOKEN", token.strip())
        os.environ["SPORTSCARDSPRO_TOKEN"] = token.strip()
        st.success("Token saved locally.")
    if ENV_PATH.exists() and os.getenv("SPORTSCARDSPRO_TOKEN"):
        st.caption("A saved token is available on this computer.")
    st.caption("API requests are automatically limited to one per second.")

(
    inventory_tab,
    purchases_tab,
    allocation_tab,
    expenses_tab,
    sales_tab,
    grading_tab,
    history_tab,
    receipts_tab,
    reports_tab,
    backup_tab,
    price_search_tab,
    add_tab,
    listing_tab,
) = st.tabs(
    [
        "Inventory",
        "Purchases",
        "Cost allocation",
        "Expenses",
        "Sales",
        "Grading",
        "History",
        "Receipts",
        "Reports",
        "Backup",
        "Price search",
        "Add a card",
        "Listing studio",
    ]
)

with purchases_tab:
    render_purchases()

with allocation_tab:
    render_allocation()

with expenses_tab:
    render_expenses()

with sales_tab:
    render_sales()

with grading_tab:
    render_grading()

with history_tab:
    render_inventory_history()

with receipts_tab:
    render_receipts()

with reports_tab:
    render_reports()

with backup_tab:
    render_backup_restore()

with price_search_tab:
    st.subheader("Search a set or parallel")
    st.caption(
        "SportsCardsPro returns at most the first 20 search matches. "
        "Use a set CSV download when you need a guaranteed complete checklist."
    )
    search_query = st.text_input(
        "SportsCardsPro search",
        value="2024 Panini Mosaic Football Black",
        help="This query is sent to SportsCardsPro.",
    )
    filter_left, filter_right = st.columns(2)
    with filter_left:
        set_filter = st.text_input(
            "Set must contain these words",
            value="2024 Panini Mosaic",
            help="Word order does not matter. Leave blank to include every set.",
        )
    with filter_right:
        parallel_filter = st.text_input(
            "Card name must contain these words",
            value="Black",
            help="For example: Black, Genesis, or Peacock.",
        )

    if st.button("Find matching cards", disabled=not (token and search_query), type="primary"):
        try:
            candidates = SportsCardsProClient(token).search(search_query)
            filtered = [
                row
                for row in candidates
                if matches_terms(row.get("console-name"), set_filter)
                and matches_parallel(row.get("product-name"), parallel_filter)
            ]
            st.session_state.price_search_candidates = filtered
            st.session_state.price_search_rows = []
            st.session_state.price_search_returned = len(candidates)
        except (SportsCardsProError, ValueError) as exc:
            st.error(str(exc))

    candidates = st.session_state.get("price_search_candidates", [])
    returned = st.session_state.get("price_search_returned")
    if returned is not None:
        st.write(f"{len(candidates)} matching cards found among {returned} API results.")
        if returned == 20:
            st.warning(
                "The API returned its maximum of 20 results, so more matching cards may exist. "
                "These results are not a complete checklist."
            )

    if candidates and st.button("Load prices for these cards"):
        client = SportsCardsProClient(token)
        rows = []
        progress = st.progress(0, text="Loading current prices...")
        try:
            for index, candidate in enumerate(candidates, start=1):
                rows.append(product_price_row(client.product(candidate["id"])))
                progress.progress(index / len(candidates), text=f"Loaded {index} of {len(candidates)} cards")
            st.session_state.price_search_rows = rows
        except (SportsCardsProError, ValueError) as exc:
            st.error(str(exc))
        finally:
            progress.empty()

    price_rows = st.session_state.get("price_search_rows", [])
    if price_rows:
        price_frame = pd.DataFrame(price_rows)
        price_columns = [column for column in price_frame.columns if column not in {"Product ID", "Card", "Set", "Annual sales"}]
        st.dataframe(
            price_frame,
            use_container_width=True,
            hide_index=True,
            column_config={column: st.column_config.NumberColumn(column, format="$%.2f") for column in price_columns},
        )
        st.download_button(
            "Download search results CSV",
            price_frame.to_csv(index=False).encode("utf-8"),
            file_name="price_hunter_search.csv",
            mime="text/csv",
        )

with add_tab:
    st.subheader("Find a card")
    search_top = st.columns([1, 1.4, 2])
    with search_top[0]:
        card_year = st.text_input("Year", placeholder="2024", key="card_search_year")
    with search_top[1]:
        card_brand = st.text_input("Brand", placeholder="Panini", key="card_search_brand")
    with search_top[2]:
        card_set = st.text_input("Set", placeholder="Mosaic Men of Mastery", key="card_search_set")

    search_bottom = st.columns([2, 1, 1.5])
    with search_bottom[0]:
        card_player = st.text_input("Player / card name", placeholder="Patrick Mahomes II", key="card_search_player")
    with search_bottom[1]:
        search_card_number = st.text_input("Card number", placeholder="MM4", key="card_search_number")
    with search_bottom[2]:
        card_parallel = st.text_input("Parallel", placeholder="White", key="card_search_parallel")

    query = build_card_search_query(
        card_year,
        card_brand,
        card_set,
        card_player,
        f"#{search_card_number.lstrip('#')}" if search_card_number.strip() else "",
        card_parallel,
    )
    if query:
        st.caption(f"SportsCardsPro query: {query}")
    if st.button("Search SportsCardsPro", disabled=not (token and query)):
        try:
            st.session_state.search_results = SportsCardsProClient(token).search(query)
            st.session_state.pop("selected_product", None)
            st.session_state.pop("add_price_basis", None)
            st.session_state.pop("add_market_value", None)
        except (SportsCardsProError, ValueError) as exc:
            st.error(str(exc))

    results = st.session_state.get("search_results", [])
    selected = None
    if results:
        labels = [f"{row.get('product-name', '')} — {row.get('console-name', '')}" for row in results]
        choice = st.selectbox("Matches", range(len(labels)), format_func=lambda i: labels[i])
        selected = results[choice]
        if st.button("Load pricing"):
            try:
                st.session_state.selected_product = SportsCardsProClient(token).product(selected["id"])
                st.session_state.pop("add_price_basis", None)
                st.session_state.pop("add_market_value", None)
            except (SportsCardsProError, ValueError) as exc:
                st.error(str(exc))

    product = st.session_state.get("selected_product", {})
    st.subheader("Card details")
    with st.container(border=True):
        left, right = st.columns(2)
        with left:
            card_name = st.text_input("Card/player name", value=product.get("product-name", ""))
            set_name = st.text_input("Set", value=product.get("console-name", ""))
            card_number = st.text_input("Card number")
            condition = st.selectbox("Card type", ["Raw / Ungraded", "Graded"])
            grader = st.selectbox("Grading company", ["", "PSA", "BGS", "SGC", "CGC", "CSG", "TAG", "Other"])
            grade = st.text_input("Numeric grade", placeholder="10")
            certification_number = st.text_input("Certification number")
        with right:
            quantity = st.number_input("Quantity", min_value=1, value=1, step=1)
            cost = st.number_input("Your cost", min_value=0.0, value=0.0, step=1.0, format="%.2f")
            guide_prices = available_prices(product)
            guide_labels = list(guide_prices) or ["No API price available"]
            preferred = "Raw / Ungraded" if condition == "Raw / Ungraded" else next(
                (label for label in guide_labels if grader and label.startswith(grader)),
                next((label for label in guide_labels if label != "Raw / Ungraded"), guide_labels[0]),
            )
            price_basis = st.selectbox(
                "SportsCardsPro price basis",
                guide_labels,
                index=guide_labels.index(preferred) if preferred in guide_labels else 0,
                key="add_price_basis",
                on_change=sync_market_value,
                args=(guide_prices,),
            )
            market_default = guide_prices.get(price_basis, 0.0)
            if "add_market_value" not in st.session_state:
                st.session_state.add_market_value = float(market_default)
            market_price = st.number_input(
                "Current market value",
                min_value=0.0,
                format="%.2f",
                key="add_market_value",
            )
            location = st.text_input("Storage location", placeholder="Box A / Row 2")
            notes = st.text_area("Condition notes")
        submitted = st.button("Add to inventory")
        if submitted:
            if not card_name.strip():
                st.error("Card/player name is required.")
            else:
                item_id = database.add_card({
                    "scp_id": str(product.get("id", "")),
                    "card_name": card_name.strip(),
                    "set_name": set_name.strip(),
                    "card_number": card_number.strip(),
                    "condition": condition,
                    "grader": grader,
                    "grade": grade.strip(),
                    "certification_number": certification_number.strip(),
                    "quantity": int(quantity),
                    "cost": float(cost),
                    "market_price": float(market_price),
                    **inventory_grade_prices(product),
                    "grade_prices_refreshed": 1 if product else 0,
                    "storage_location": location.strip(),
                    "notes": notes.strip(),
                })
                st.success(f"Added inventory item PH-{item_id:06d}.")

with inventory_tab:
    if st.session_state.get("price_refresh_message"):
        st.success(st.session_state.pop("price_refresh_message"))
    with st.expander("Import SportsCardsPro collection CSV"):
        st.write(
            "Upload a collection CSV exported by SportsCardsPro. Prices and costs are converted "
            "from pennies to dollars; raw and graded cards are mapped automatically."
        )
        collection_files = st.file_uploader(
            "Collection CSV files",
            type="csv",
            accept_multiple_files=True,
            key="scp_collection_files",
        )
        import_rows = []
        import_errors = []
        for collection_file in collection_files or []:
            try:
                source_frame = pd.read_csv(collection_file, dtype=str, keep_default_na=False)
                missing = REQUIRED_COLUMNS - set(source_frame.columns)
                if missing:
                    import_errors.append(f"{collection_file.name}: missing {', '.join(sorted(missing))}")
                    continue
                import_rows.extend(collection_row_to_card(row) for row in source_frame.to_dict("records"))
            except Exception as exc:
                import_errors.append(f"{collection_file.name}: {exc}")

        for error in import_errors:
            st.error(error)

        if import_rows:
            existing_keys = {import_identity(card) for card in database.all_cards()}
            unique_rows = []
            seen_keys = set(existing_keys)
            for row in import_rows:
                identity = import_identity(row)
                if identity not in seen_keys:
                    unique_rows.append(row)
                    seen_keys.add(identity)
            duplicates = len(import_rows) - len(unique_rows)
            st.write(
                f"Ready to import {len(unique_rows):,} cards. "
                f"{duplicates:,} duplicate rows will be skipped."
            )
            preview = pd.DataFrame(unique_rows[:10])
            if not preview.empty:
                st.dataframe(
                    preview[["card_name", "set_name", "condition", "grader", "grade", "quantity", "cost", "market_price"]],
                    use_container_width=True,
                    hide_index=True,
                )
            if st.button("Import collection", disabled=not unique_rows, type="primary"):
                imported_count = database.add_cards(unique_rows)
                st.success(f"Imported {imported_count:,} cards into Price Hunter.")

    with st.expander("Import TCG Collector Pokemon CSV"):
        st.write(
            "Imports missing Pokemon cards and quantity differences. Existing matching cards are "
            "recognized by set, card name, number, variant, and inventory condition."
        )
        tcg_file = st.file_uploader(
            "TCG Collector collection export", type="csv", key="tcgcollector_collection_file"
        )
        if tcg_file is not None:
            try:
                tcg_frame = pd.read_csv(tcg_file, dtype=str, keep_default_na=False)
                tcg_missing = TCGCOLLECTOR_REQUIRED_COLUMNS - set(tcg_frame.columns)
                if tcg_missing:
                    st.error(f"Missing required columns: {', '.join(sorted(tcg_missing))}")
                else:
                    tcg_rows = [
                        tcgcollector_row_to_card(row) for row in tcg_frame.to_dict("records")
                    ]
                    tcg_additions, tcg_stats = reconcile_collection_rows(
                        tcg_rows, database.all_cards()
                    )
                    st.write(
                        f"Export: {tcg_stats['export_rows']:,} unique cards / "
                        f"{tcg_stats['export_units']:,} copies. Existing inventory covers "
                        f"{tcg_stats['covered_units']:,} copies. Ready to add "
                        f"{tcg_stats['new_units']:,} copies across {tcg_stats['new_rows']:,} rows."
                    )
                    if tcg_additions:
                        tcg_preview = pd.DataFrame(tcg_additions[:25])
                        st.dataframe(
                            tcg_preview[
                                ["card_name", "set_name", "card_number", "quantity", "market_price", "notes"]
                            ],
                            use_container_width=True, hide_index=True,
                            column_config={
                                "market_price": st.column_config.NumberColumn(
                                    "TCG Collector price", format="$%.2f"
                                )
                            },
                        )
                    if st.button(
                        "Import missing Pokemon cards",
                        disabled=not tcg_additions, type="primary", key="import_tcgcollector",
                    ):
                        imported_count = database.add_cards(tcg_additions)
                        st.success(
                            f"Imported {imported_count:,} inventory rows / "
                            f"{tcg_stats['new_units']:,} physical cards."
                        )
                        st.rerun()
            except Exception as exc:
                st.error(f"Could not read TCG Collector export: {exc}")

    cards = database.all_cards()
    if not cards:
        st.info("Your inventory is empty. Use ‘Add a card’ to create the first item.")
    else:
        frame = pd.DataFrame(cards)

        with st.expander("Filter inventory", expanded=True):
            filter_row_1 = st.columns(5)
            with filter_row_1[0]:
                type_filter = st.multiselect(
                    "Type",
                    sorted({collection_type(value) for value in frame["set_name"] if collection_type(value)}),
                    key="inventory_type_filter",
                )
            with filter_row_1[1]:
                name_filter = st.text_input("Player / card name", key="inventory_name_filter")
            with filter_row_1[2]:
                year_filter = st.text_input("Year", key="inventory_year_filter")
            with filter_row_1[3]:
                brand_filter = st.text_input("Brand", key="inventory_brand_filter")
            with filter_row_1[4]:
                set_name_filter = st.text_input("Set", key="inventory_set_filter")

            filter_row_2 = st.columns(4)
            with filter_row_2[0]:
                parallel_inventory_filter = st.text_input("Parallel", key="inventory_parallel_filter")
            with filter_row_2[1]:
                number_filter = st.text_input("Card number", key="inventory_number_filter")
            with filter_row_2[2]:
                condition_filter = st.multiselect(
                    "Card type",
                    sorted(value for value in frame["condition"].dropna().unique() if value),
                    key="inventory_condition_filter",
                )
            with filter_row_2[3]:
                location_filter = st.multiselect(
                    "Storage location",
                    sorted(value for value in frame["storage_location"].dropna().unique() if value),
                    key="inventory_location_filter",
                )

            filter_row_3 = st.columns(5)
            with filter_row_3[0]:
                price_basis_label = st.selectbox(
                    "Price basis",
                    ["Market / Raw", "Graded 8 / 8.5", "Graded 9", "PSA 10"],
                    key="inventory_price_basis_filter",
                )
                price_basis_columns = {
                    "Market / Raw": "market_price",
                    "Graded 8 / 8.5": "graded_8_price",
                    "Graded 9": "graded_9_price",
                    "PSA 10": "psa_10_price",
                }
                price_basis_column = price_basis_columns[price_basis_label]
            with filter_row_3[1]:
                minimum_price = st.number_input(
                    "Minimum price", min_value=0.0, value=0.0,
                    format="%.2f", key="inventory_min_price",
                )
            with filter_row_3[2]:
                maximum_price = st.number_input(
                    "Maximum price", min_value=0.0, value=0.0,
                    format="%.2f", help="Leave at 0 for no maximum.",
                    key="inventory_max_price",
                )
            with filter_row_3[3]:
                price_coverage_filter = st.selectbox(
                    "Grading-price status", ["All", "Fetched", "Not fetched"],
                    key="inventory_price_coverage_filter",
                )
            with filter_row_3[4]:
                status_filter = st.multiselect(
                    "Inventory status",
                    sorted(value for value in frame["status"].dropna().unique() if value),
                    key="inventory_status_filter",
                )

        def filter_text(data: pd.DataFrame, column: str, text: str) -> pd.DataFrame:
            if not text.strip():
                return data
            return data[data[column].fillna("").map(lambda value: matches_terms(value, text))]

        filtered_frame = frame.copy()
        if type_filter:
            filtered_frame = filtered_frame[
                filtered_frame["set_name"].map(collection_type).isin(type_filter)
            ]
        filtered_frame = filter_text(filtered_frame, "card_name", name_filter)
        filtered_frame = filter_text(filtered_frame, "set_name", year_filter)
        filtered_frame = filter_text(filtered_frame, "set_name", brand_filter)
        filtered_frame = filter_text(filtered_frame, "set_name", set_name_filter)
        if parallel_inventory_filter.strip():
            filtered_frame = filtered_frame[
                filtered_frame["card_name"].fillna("").map(
                    lambda value: matches_parallel(value, parallel_inventory_filter)
                )
            ]
        filtered_frame = filter_text(filtered_frame, "card_number", number_filter)
        if condition_filter:
            filtered_frame = filtered_frame[filtered_frame["condition"].isin(condition_filter)]
        if location_filter:
            filtered_frame = filtered_frame[filtered_frame["storage_location"].isin(location_filter)]
        if status_filter:
            filtered_frame = filtered_frame[filtered_frame["status"].isin(status_filter)]
        if minimum_price:
            filtered_frame = filtered_frame[
                filtered_frame[price_basis_column].fillna(0) >= minimum_price
            ]
        if maximum_price:
            filtered_frame = filtered_frame[
                filtered_frame[price_basis_column].fillna(0) <= maximum_price
            ]
        if price_coverage_filter == "Fetched":
            filtered_frame = filtered_frame[filtered_frame["grade_prices_refreshed"] == 1]
        elif price_coverage_filter == "Not fetched":
            filtered_frame = filtered_frame[filtered_frame["grade_prices_refreshed"] == 0]
        row_cost_totals = filtered_frame["allocated_cost_total"].fillna(
            filtered_frame["cost"] * filtered_frame["quantity"]
        )
        total_cost = row_cost_totals.sum()
        total_market = (filtered_frame["market_price"].fillna(0) * filtered_frame["quantity"]).sum()
        total_grade_9 = (filtered_frame["graded_9_price"].fillna(0) * filtered_frame["quantity"]).sum()
        total_psa_10 = (filtered_frame["psa_10_price"].fillna(0) * filtered_frame["quantity"]).sum()
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Inventory items", len(filtered_frame), f"{len(filtered_frame):,} of {len(frame):,}")
        c2.metric("Total cost", f"${total_cost:,.2f}")
        c3.metric("Market estimate", f"${total_market:,.2f}", f"${total_market-total_cost:,.2f}")
        c4.metric("Grade 9 estimate", f"${total_grade_9:,.2f}", f"${total_grade_9-total_cost:,.2f}")
        c5.metric("PSA 10 estimate", f"${total_psa_10:,.2f}", f"${total_psa_10-total_cost:,.2f}")
        refreshed_count = int(frame["grade_prices_refreshed"].fillna(0).sum())
        coverage = refreshed_count / len(frame) if len(frame) else 0
        refresh_dates = frame["grade_prices_refreshed_at"].dropna()
        last_refreshed = refresh_dates.max() if not refresh_dates.empty else "Never"
        st.caption(
            f"Grading-price coverage: {refreshed_count:,} of {len(frame):,} cards ({coverage:.1%}). "
            f"Last completed API refresh: {last_refreshed}."
        )
        display_columns = ["sku", "card_name", "set_name", "card_number", "condition", "grader", "grade", "grading_status", "quantity", "cost", "allocated_cost_total", "market_price", "graded_8_price", "graded_9_price", "psa_10_price", "grade_prices_refreshed_at", "list_price", "status", "storage_location"]
        inventory_event = st.dataframe(
            filtered_frame[display_columns],
            use_container_width=True,
            hide_index=True,
            key="inventory_grid",
            on_select="rerun",
            selection_mode="multi-row",
            column_config={
                "graded_8_price": st.column_config.NumberColumn("Graded 8 / 8.5", format="$%.2f"),
                "graded_9_price": st.column_config.NumberColumn("Graded 9", format="$%.2f"),
                "psa_10_price": st.column_config.NumberColumn("PSA 10", format="$%.2f"),
                "allocated_cost_total": st.column_config.NumberColumn("Allocated cost total", format="$%.2f"),
                "grade_prices_refreshed_at": st.column_config.DatetimeColumn("Prices refreshed"),
            },
        )
        selected_positions = inventory_event.selection.rows
        selected_cards = [
            filtered_frame.iloc[position].to_dict()
            for position in selected_positions
            if position < len(filtered_frame)
        ]
        selected_ids = [int(card["id"]) for card in selected_cards]
        if selected_cards:
            st.caption(f"{len(selected_cards):,} inventory row(s) selected.")
            selection_actions = st.columns(2)
            with selection_actions[0]:
                if st.button("Add selected cards to listing queue"):
                    st.session_state.listing_queue_ids = selected_ids
                    st.success(f"Added {len(selected_ids):,} cards to the listing queue.")
            with selection_actions[1]:
                refreshable = [card for card in selected_cards if card.get("scp_id")]
                if st.button(
                    "Refresh selected card prices",
                    disabled=price_refresh.is_running() or not (token and refreshable),
                ):
                    price_refresh.start(refreshable, token)
                    st.rerun()

        with st.expander("Edit, bulk update, or delete selected inventory"):
            if not selected_cards:
                st.info("Select one or more rows in the inventory table.")
            else:
                if len(selected_cards) == 1:
                    edit_card = selected_cards[0]
                    st.markdown("#### Edit selected card")
                    edit_left, edit_right = st.columns(2)
                    with edit_left:
                        edit_name = st.text_input("Card name", value=edit_card["card_name"], key=f"edit_card_name_{edit_card['id']}")
                        edit_set = st.text_input("Set name", value=edit_card["set_name"], key=f"edit_set_name_{edit_card['id']}")
                        edit_number = st.text_input("Card number", value=edit_card["card_number"], key=f"edit_card_number_{edit_card['id']}")
                        condition_options = ["Raw / Ungraded", "Graded", "Sealed", "Other"]
                        current_condition = edit_card.get("condition") or "Raw / Ungraded"
                        if current_condition not in condition_options:
                            condition_options.append(current_condition)
                        edit_condition = st.selectbox(
                            "Condition",
                            condition_options,
                            index=condition_options.index(current_condition),
                            key=f"edit_condition_{edit_card['id']}",
                        )
                        if edit_condition == "Graded":
                            edit_grader = st.selectbox(
                                "Grading company",
                                ["PSA", "SGC", "BGS", "CGC", "Other"],
                                index=["PSA", "SGC", "BGS", "CGC", "Other"].index(edit_card["grader"])
                                if edit_card.get("grader") in ["PSA", "SGC", "BGS", "CGC", "Other"] else 0,
                                key=f"edit_grader_{edit_card['id']}",
                            )
                            edit_grade = st.text_input(
                                "Numeric grade", value=str(edit_card.get("grade") or ""),
                                key=f"edit_grade_{edit_card['id']}",
                            )
                            edit_certification = st.text_input(
                                "Certification number",
                                value=edit_card.get("certification_number", ""),
                                key=f"edit_certification_{edit_card['id']}",
                            )
                        else:
                            edit_grader = ""
                            edit_grade = ""
                            edit_certification = ""
                        edit_location = st.text_input(
                            "Storage location", value=edit_card["storage_location"], key=f"edit_location_{edit_card['id']}"
                        )
                    with edit_right:
                        edit_quantity = st.number_input(
                            "Quantity", min_value=0, value=int(edit_card["quantity"]), step=1, key=f"edit_quantity_{edit_card['id']}"
                        )
                        edit_cost = st.number_input(
                            "Cost per card", min_value=0.0, value=float(edit_card["cost"]), format="%.2f",
                            key=f"edit_cost_{edit_card['id']}",
                        )
                        edit_status = st.selectbox(
                            "Status", ["Draft", "Ready", "Listed", "Grading", "Sold"],
                            index=["Draft", "Ready", "Listed", "Grading", "Sold"].index(edit_card["status"])
                            if edit_card["status"] in ["Draft", "Ready", "Listed", "Grading", "Sold"] else 0,
                            key=f"edit_status_{edit_card['id']}",
                        )
                        edit_images = st.text_area(
                            "Public image URLs (one per line)",
                            value=edit_card.get("image_urls", ""),
                            key=f"edit_images_{edit_card['id']}",
                        )
                    if st.button("Save selected card"):
                        database.update_card(int(edit_card["id"]), {
                            "card_name": edit_name.strip(),
                            "set_name": edit_set.strip(),
                            "card_number": edit_number.strip(),
                            "condition": edit_condition,
                            "grader": edit_grader,
                            "grade": edit_grade.strip(),
                            "certification_number": edit_certification.strip(),
                            "storage_location": edit_location.strip(),
                            "quantity": int(edit_quantity),
                            "cost": float(edit_cost),
                            "status": edit_status,
                            "image_urls": edit_images.strip(),
                        })
                        st.rerun()

                st.markdown("#### Bulk changes")
                bulk_left, bulk_right = st.columns(2)
                bulk_values = {}
                with bulk_left:
                    if st.checkbox("Change storage location"):
                        bulk_values["storage_location"] = st.text_input("New storage location").strip()
                    if st.checkbox("Change set name"):
                        bulk_values["set_name"] = st.text_input("New set name").strip()
                with bulk_right:
                    if st.checkbox("Change status"):
                        bulk_values["status"] = st.selectbox(
                            "New status", ["Draft", "Ready", "Listed", "Grading", "Sold"], key="bulk_status"
                        )
                    if st.checkbox("Change quantity"):
                        bulk_values["quantity"] = int(st.number_input(
                            "New quantity", min_value=0, value=1, step=1, key="bulk_quantity"
                        ))
                if st.button("Apply bulk changes", disabled=not bulk_values):
                    database.update_cards(selected_ids, bulk_values)
                    st.rerun()

                if len(selected_cards) == 1 and int(selected_cards[0]["quantity"]) > 0:
                    sold_card = selected_cards[0]
                    st.markdown("#### Mark as sold")
                    st.caption(
                        "Creates a permanent sales record, calculates profit, reduces quantity, "
                        "and records the transaction in inventory history."
                    )
                    sale_row_1 = st.columns(4)
                    sale_date = sale_row_1[0].date_input(
                        "Sale date", key=f"sold_date_{sold_card['id']}"
                    )
                    marketplace = sale_row_1[1].selectbox(
                        "Marketplace", ["Direct", "Card show", "Facebook", "eBay", "Whatnot", "Other"],
                        key=f"sold_marketplace_{sold_card['id']}",
                    )
                    sale_quantity = int(sale_row_1[2].number_input(
                        "Quantity sold", min_value=1, max_value=int(sold_card["quantity"]),
                        value=1, step=1, key=f"sold_quantity_{sold_card['id']}",
                    ))
                    buyer = sale_row_1[3].text_input("Buyer", key=f"sold_buyer_{sold_card['id']}")
                    payment_row = st.columns(3)
                    payment_method = payment_row[0].selectbox(
                        "Payment method", ["Cash", "Trade-in value", "Cash + trade"],
                        key=f"sold_payment_method_{sold_card['id']}",
                    )
                    cash_received = payment_row[1].number_input(
                        "Cash received", min_value=0.0, format="%.2f",
                        disabled=payment_method == "Trade-in value",
                        key=f"sold_cash_received_{sold_card['id']}",
                    )
                    trade_value = payment_row[2].number_input(
                        "Trade-in value", min_value=0.0, format="%.2f",
                        disabled=payment_method == "Cash",
                        key=f"sold_trade_value_{sold_card['id']}",
                    )
                    effective_cash = 0.0 if payment_method == "Trade-in value" else cash_received
                    effective_trade = 0.0 if payment_method == "Cash" else trade_value
                    sale_price = effective_cash + effective_trade
                    sale_row_2 = st.columns(4)
                    shipping_charged = sale_row_2[0].number_input(
                        "Shipping charged", min_value=0.0, format="%.2f",
                        key=f"sold_shipping_charged_{sold_card['id']}",
                    )
                    fees = sale_row_2[1].number_input(
                        "Platform fees", min_value=0.0, format="%.2f",
                        key=f"sold_fees_{sold_card['id']}",
                    )
                    promoted_fees = sale_row_2[2].number_input(
                        "Promoted listing fees", min_value=0.0, format="%.2f",
                        key=f"sold_promoted_{sold_card['id']}",
                    )
                    label_cost = sale_row_2[3].number_input(
                        "Shipping label cost", min_value=0.0, format="%.2f",
                        key=f"sold_label_{sold_card['id']}",
                    )
                    sale_row_3 = st.columns(3)
                    tax_collected = sale_row_3[1].number_input(
                        "Tax collected", min_value=0.0, format="%.2f",
                        key=f"sold_tax_{sold_card['id']}",
                    )
                    order_number = sale_row_3[0].text_input(
                        "Order number", key=f"sold_order_{sold_card['id']}"
                    )
                    item_id = sale_row_3[2].text_input(
                        "Marketplace item ID", value=sold_card.get("ebay_item_id", ""),
                        key=f"sold_item_id_{sold_card['id']}",
                    )
                    sale_notes = st.text_area("Sale notes", key=f"sold_notes_{sold_card['id']}")
                    estimated_net = sale_price + shipping_charged - fees - promoted_fees - label_cost
                    estimated_cogs = float(sold_card["cost"] or 0) * sale_quantity
                    st.caption(
                        f"Estimated net: ${estimated_net:,.2f} · "
                        f"COGS: ${estimated_cogs:,.2f} · "
                        f"Profit: ${estimated_net - estimated_cogs:,.2f}"
                    )
                    confirm_sale = st.checkbox(
                        "Confirm this sale and reduce inventory",
                        key=f"sold_confirm_{sold_card['id']}",
                    )
                    if st.button(
                        "Complete sale", disabled=not confirm_sale,
                        type="primary", key=f"sold_submit_{sold_card['id']}",
                    ):
                        database.create_manual_sale(int(sold_card["id"]), {
                            "sale_date": sale_date.isoformat(),
                            "marketplace": marketplace,
                            "order_number": order_number.strip(),
                            "item_id": item_id.strip(),
                            "title": sold_card["card_name"],
                            "quantity": sale_quantity,
                            "item_subtotal": round(sale_price, 2),
                            "payment_method": payment_method,
                            "cash_received": round(effective_cash, 2),
                            "trade_value": round(effective_trade, 2),
                            "shipping_charged": round(shipping_charged, 2),
                            "fees": round(fees, 2),
                            "shipping_label_cost": round(label_cost, 2),
                            "buyer": buyer.strip(),
                            "tax_collected": round(tax_collected, 2),
                            "promoted_listing_fees": round(promoted_fees, 2),
                            "status": "Completed",
                            "notes": sale_notes.strip(),
                        })
                        st.success("Sale recorded and inventory updated.")
                        st.rerun()

                st.markdown("#### Delete")
                confirm_delete = st.checkbox(
                    f"I understand this will permanently delete {len(selected_ids):,} inventory row(s)."
                )
                if st.button("Delete selected inventory", disabled=not confirm_delete, type="secondary"):
                    database.delete_cards(selected_ids)
                    st.rerun()

        with st.expander("Refresh grading prices from SportsCardsPro"):
            st.caption(
                "Refreshes run in the background at SportsCardsPro's one-request-per-second limit. "
                "You can use other tabs while the job runs."
            )
            refresh_choices = {
                f"{card['sku']} — {card['card_name']}": card for card in cards
            }
            missing_cards = [
                card for card in cards
                if not card.get("grade_prices_refreshed")
            ]
            st.write(f"{len(missing_cards):,} inventory cards have not had grading prices fetched yet.")
            unresolved_cards = [card for card in missing_cards if not card.get("scp_id")]
            if unresolved_cards:
                st.info(
                    f"{len(unresolved_cards):,} cards have no SportsCardsPro ID. Refresh will search "
                    "for an exact set, card name, number, and variant match before fetching prices."
                )
            batch_size = st.number_input(
                "Small batch size",
                min_value=1,
                max_value=100,
                value=25,
                help="Useful for a quick partial update. The full refresh has no 100-card ceiling.",
            )
            if missing_cards:
                estimated_minutes = len(missing_cards) * 1.05 / 60
                st.caption(
                    f"Refreshing all {len(missing_cards):,} missing cards will take approximately "
                    f"{estimated_minutes:,.0f} minutes."
                )

            @st.fragment(run_every=2)
            def refresh_status_panel() -> None:
                job = price_refresh.get_status()
                if job["state"] in {"running", "pausing"}:
                    progress_value = job["processed"] / job["total"] if job["total"] else 0
                    st.progress(
                        progress_value,
                        text=f"{job['state'].title()}: {job['processed']:,} of {job['total']:,}",
                    )
                    if job["current"]:
                        st.caption(f"Current: {job['current']}")
                elif job["state"] == "completed":
                    st.success(f"Background refresh completed: {job['succeeded']:,} cards updated.")
                elif job["state"] == "completed_with_errors":
                    st.warning(
                        f"Refresh finished: {job['succeeded']:,} updated and {job['failed']:,} "
                        f"could not be matched. Last issue: {job['last_error']}"
                    )
                elif job["state"] == "paused":
                    st.warning(f"Refresh paused after {job['succeeded']:,} successful updates.")
                elif job["state"] == "error":
                    st.error(f"Refresh stopped: {job['last_error']}")

            refresh_status_panel()
            job_running = price_refresh.is_running()
            refresh_buttons = st.columns(4)

            with refresh_buttons[0]:
                if st.button(
                    "Start small batch",
                    disabled=job_running or not (token and missing_cards),
                    type="primary",
                ):
                    price_refresh.start(missing_cards[: int(batch_size)], token)
                    st.rerun()

            with refresh_buttons[1]:
                if st.button(
                    "Refresh all missing",
                    disabled=job_running or not (token and missing_cards),
                ):
                    price_refresh.start(missing_cards, token)
                    st.rerun()

            with refresh_buttons[2]:
                if st.button("Pause refresh", disabled=not job_running):
                    price_refresh.pause()
                    st.rerun()

            with refresh_buttons[3]:
                if st.button("Reload inventory values"):
                    st.rerun()

            selected_refresh = st.multiselect("Refresh specific cards", refresh_choices)
            if st.button(
                "Start selected refresh",
                disabled=job_running or not (token and selected_refresh),
            ):
                price_refresh.start([refresh_choices[label] for label in selected_refresh], token)
                st.rerun()
        st.download_button(
            "Export filtered inventory CSV",
            filtered_frame.to_csv(index=False).encode("utf-8"),
            file_name="price_hunter_inventory_filtered.csv",
            mime="text/csv",
        )

with listing_tab:
    cards = database.all_cards()
    with st.expander("eBay API connection and policies"):
        ebay_environment = st.selectbox(
            "Environment",
            ["Sandbox", "Production"],
            index=0 if os.getenv("EBAY_ENVIRONMENT", "Sandbox") == "Sandbox" else 1,
        )
        ebay_columns = st.columns(2)
        with ebay_columns[0]:
            ebay_client_id = st.text_input("Client ID", value=os.getenv("EBAY_CLIENT_ID", ""))
            ebay_client_secret = st.text_input(
                "Client secret", value=os.getenv("EBAY_CLIENT_SECRET", ""), type="password"
            )
            ebay_refresh_token = st.text_area(
                "User refresh token", value=os.getenv("EBAY_REFRESH_TOKEN", "")
            )
            ebay_access_token = st.text_area(
                "Temporary user access token", value=os.getenv("EBAY_ACCESS_TOKEN", ""),
                help="Optional when client credentials and a refresh token are configured.",
            )
        with ebay_columns[1]:
            ebay_marketplace = st.text_input(
                "Marketplace ID", value=os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US")
            )
            ebay_location = st.text_input(
                "Merchant location key", value=os.getenv("EBAY_LOCATION_KEY", "")
            )
            ebay_category = st.text_input(
                "Trading-card category ID", value=os.getenv("EBAY_CATEGORY_ID", "")
            )
            ebay_fulfillment = st.text_input(
                "Fulfillment policy ID", value=os.getenv("EBAY_FULFILLMENT_POLICY_ID", "")
            )
            ebay_payment = st.text_input(
                "Payment policy ID", value=os.getenv("EBAY_PAYMENT_POLICY_ID", "")
            )
            ebay_return = st.text_input(
                "Return policy ID", value=os.getenv("EBAY_RETURN_POLICY_ID", "")
            )
            ebay_condition = st.text_input(
                "eBay condition enum",
                value=os.getenv("EBAY_CONDITION", ""),
                help="Trading-card condition requirements vary by category; enter the enum required by eBay.",
            )

        ebay_config = EbayConfig(
            environment=ebay_environment,
            client_id=ebay_client_id.strip(),
            client_secret=ebay_client_secret.strip(),
            refresh_token=ebay_refresh_token.strip(),
            access_token=ebay_access_token.strip(),
            marketplace_id=ebay_marketplace.strip(),
            merchant_location_key=ebay_location.strip(),
            category_id=ebay_category.strip(),
            fulfillment_policy_id=ebay_fulfillment.strip(),
            payment_policy_id=ebay_payment.strip(),
            return_policy_id=ebay_return.strip(),
            condition=ebay_condition.strip(),
        )
        ebay_buttons = st.columns(2)
        with ebay_buttons[0]:
            if st.button("Save eBay settings locally"):
                settings = {
                    "EBAY_ENVIRONMENT": ebay_environment,
                    "EBAY_CLIENT_ID": ebay_client_id.strip(),
                    "EBAY_CLIENT_SECRET": ebay_client_secret.strip(),
                    "EBAY_REFRESH_TOKEN": ebay_refresh_token.strip(),
                    "EBAY_ACCESS_TOKEN": ebay_access_token.strip(),
                    "EBAY_MARKETPLACE_ID": ebay_marketplace.strip(),
                    "EBAY_LOCATION_KEY": ebay_location.strip(),
                    "EBAY_CATEGORY_ID": ebay_category.strip(),
                    "EBAY_FULFILLMENT_POLICY_ID": ebay_fulfillment.strip(),
                    "EBAY_PAYMENT_POLICY_ID": ebay_payment.strip(),
                    "EBAY_RETURN_POLICY_ID": ebay_return.strip(),
                    "EBAY_CONDITION": ebay_condition.strip(),
                }
                for setting, value in settings.items():
                    set_key(str(ENV_PATH), setting, value)
                    os.environ[setting] = value
                st.success("eBay settings saved in the private local .env file.")
        with ebay_buttons[1]:
            if st.button(
                "Test eBay connection",
                disabled=not (ebay_access_token or (ebay_client_id and ebay_client_secret and ebay_refresh_token)),
            ):
                try:
                    version = EbayClient(ebay_config).test_connection()
                    st.success(f"Connected to eBay {ebay_environment}: {version}")
                except EbayError as exc:
                    st.error(str(exc))
    if not cards:
        st.info("Add a card before creating a listing.")
    else:
        choices = {f"{card['sku']} — {card['card_name']}": card for card in cards}
        queued_ids = set(st.session_state.get("listing_queue_ids", []))
        default_queue = [choice_label for choice_label, item in choices.items() if item["id"] in queued_ids]
        queue_labels = st.multiselect(
            "Listing queue",
            choices,
            default=default_queue,
            help="Select multiple cards here or send selected rows from Inventory.",
        )
        queue_cards = [choices[choice_label] for choice_label in queue_labels]
        queue_markup = st.number_input(
            "Queue minimum markup over cost (%)",
            min_value=0.0,
            value=30.0,
            step=5.0,
            key="queue_markup",
        )
        if st.button("Generate drafts for listing queue", disabled=not queue_cards):
            for queued_card in queue_cards:
                database.update_card(queued_card["id"], {
                    "listing_title": queued_card["listing_title"] or build_title(
                        queued_card["card_name"], queued_card["set_name"], queued_card["grade"],
                        queued_card["card_number"], queued_card["grader"],
                    ),
                    "listing_description": queued_card["listing_description"] or build_description(
                        queued_card["card_name"], queued_card["set_name"], queued_card["condition"],
                        queued_card["grade"], queued_card["notes"], queued_card["sku"],
                        queued_card["grader"], queued_card["certification_number"],
                    ),
                    "list_price": queued_card["list_price"] or suggested_price(
                        queued_card["market_price"], queued_card["cost"], queue_markup
                    ),
                    "status": "Draft",
                })
            st.success(f"Generated {len(queue_cards):,} listing drafts.")
            st.rerun()

        if queue_cards:
            prepared_queue = []
            for item in queue_cards:
                prepared = {
                    **item,
                    "listing_title": item["listing_title"] or build_title(
                        item["card_name"], item["set_name"], item["grade"],
                        item["card_number"], item["grader"],
                    ),
                    "listing_description": item["listing_description"] or build_description(
                        item["card_name"], item["set_name"], item["condition"],
                        item["grade"], item["notes"], item["sku"],
                        item["grader"], item["certification_number"],
                    ),
                    "list_price": item["list_price"] or suggested_price(
                        item["market_price"], item["cost"], queue_markup
                    ),
                }
                prepared["readiness_issues"] = listing_readiness_issues(prepared)
                prepared_queue.append(prepared)
            queue_frame = pd.DataFrame([
                {
                    "sku": item["sku"],
                    "title": item["listing_title"],
                    "price": item["list_price"],
                    "quantity": item["quantity"],
                    "status": "Ready" if not item["readiness_issues"] else "Needs work",
                    "missing": "; ".join(item["readiness_issues"]),
                }
                for item in prepared_queue
            ])
            st.markdown("#### Listing queue readiness")
            st.dataframe(
                queue_frame,
                use_container_width=True,
                hide_index=True,
                column_config={"price": st.column_config.NumberColumn("Price", format="$%.2f")},
            )
            ready_queue = [item for item in prepared_queue if not item["readiness_issues"]]
            draft_export = pd.DataFrame([ebay_draft_row(item) for item in prepared_queue])
            ready_export = pd.DataFrame([ebay_draft_row(item) for item in ready_queue])
            download_columns = st.columns(2)
            download_columns[0].download_button(
                "Download listing queue CSV",
                draft_export.to_csv(index=False).encode("utf-8"),
                file_name="price_hunter_ebay_drafts.csv",
                mime="text/csv",
            )
            download_columns[1].download_button(
                "Download ready listings CSV",
                ready_export.to_csv(index=False).encode("utf-8"),
                file_name="price_hunter_ebay_ready.csv",
                mime="text/csv",
                disabled=not ready_queue,
            )

        editor_choices = {choice_label: choices[choice_label] for choice_label in queue_labels} if queue_labels else choices
        label = st.selectbox("Edit inventory item", editor_choices)
        card = editor_choices[label]
        markup = st.number_input("Minimum markup over cost (%)", min_value=0.0, value=30.0, step=5.0)
        default_title = card["listing_title"] or build_title(
            card["card_name"], card["set_name"], card["grade"], card["card_number"], card["grader"]
        )
        default_description = card["listing_description"] or build_description(
            card["card_name"], card["set_name"], card["condition"], card["grade"], card["notes"], card["sku"],
            card["grader"], card["certification_number"]
        )
        default_price = card["list_price"] or suggested_price(card["market_price"], card["cost"], markup)
        with st.form("listing_draft"):
            title = st.text_input("eBay title", value=default_title, max_chars=80)
            st.caption(f"{len(title)}/80 characters")
            description = st.text_area("Description", value=default_description, height=260)
            list_price = st.number_input("Buy It Now price", min_value=0.0, value=float(default_price), format="%.2f")
            status = st.selectbox("Status", ["Draft", "Ready", "Listed", "Sold"], index=["Draft", "Ready", "Listed", "Sold"].index(card["status"]) if card["status"] in ["Draft", "Ready", "Listed", "Sold"] else 0)
            ebay_item_id = st.text_input("eBay item number", value=card["ebay_item_id"])
            ebay_offer_id = st.text_input("eBay offer ID", value=card.get("ebay_offer_id", ""))
            image_urls = st.text_area(
                "Public image URLs (one per line)",
                value=card.get("image_urls", ""),
                help="eBay must be able to download these images from public HTTPS URLs.",
            )
            if st.form_submit_button("Save listing draft"):
                database.update_card(card["id"], {
                    "listing_title": title.strip(),
                    "listing_description": description.strip(),
                    "list_price": float(list_price),
                    "status": status,
                    "ebay_item_id": ebay_item_id.strip(),
                    "ebay_offer_id": ebay_offer_id.strip(),
                    "image_urls": image_urls.strip(),
                })
                st.success("Listing draft saved.")

        st.markdown("#### Publish through eBay Inventory API")
        publish_card = {
            **card,
            "listing_title": title.strip(),
            "listing_description": description.strip(),
            "list_price": float(list_price),
            "image_urls": image_urls.strip(),
        }
        publish_issues = listing_readiness_issues(publish_card, ebay_config)
        publish_ready = not publish_issues
        if publish_issues:
            with st.expander("Publishing checklist", expanded=True):
                for issue in publish_issues:
                    st.warning(issue)
        publish_confirmation = st.checkbox(
            f"I confirm this will create a listing in eBay {ebay_environment}.",
            key=f"publish_confirm_{card['id']}",
        )
        if st.button(
            f"Publish {card['sku']} to eBay {ebay_environment}",
            disabled=not (publish_ready and publish_confirmation),
            type="primary",
        ):
            try:
                inventory_payload, offer_payload = listing_payloads(publish_card, ebay_config)
                ebay_client = EbayClient(ebay_config)
                ebay_client.create_inventory_item(card["sku"], inventory_payload)
                offer_id = ebay_offer_id.strip() or ebay_client.create_offer(offer_payload)
                listing_id = ebay_client.publish_offer(offer_id)
                database.update_card(card["id"], {
                    "ebay_offer_id": offer_id,
                    "ebay_item_id": listing_id,
                    "listing_title": title.strip(),
                    "listing_description": description.strip(),
                    "list_price": float(list_price),
                    "image_urls": image_urls.strip(),
                    "status": "Listed",
                })
                st.success(f"Published eBay listing {listing_id}.")
                st.rerun()
            except EbayError as exc:
                st.error(str(exc))
