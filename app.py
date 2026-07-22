from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv, set_key

import database
from listings import build_description, build_title, suggested_price
from scp_import import REQUIRED_COLUMNS, collection_row_to_card, import_identity
from sportscardspro import (
    SportsCardsProClient,
    SportsCardsProError,
    available_prices,
    build_card_search_query,
    inventory_grade_prices,
    matches_terms,
    product_price_row,
)


ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(ENV_PATH)
database.initialize()


def sync_market_value(guide_prices: dict[str, float]) -> None:
    """Keep market value aligned with the selected SportsCardsPro price."""
    selected_basis = st.session_state.get("add_price_basis")
    st.session_state.add_market_value = float(guide_prices.get(selected_basis, 0.0))

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

inventory_tab, price_search_tab, add_tab, listing_tab = st.tabs(
    ["Inventory", "Price search", "Add a card", "Listing studio"]
)

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
                and matches_terms(row.get("product-name"), parallel_filter)
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

    cards = database.all_cards()
    if not cards:
        st.info("Your inventory is empty. Use ‘Add a card’ to create the first item.")
    else:
        frame = pd.DataFrame(cards)
        total_cost = (frame["cost"] * frame["quantity"]).sum()
        total_market = (frame["market_price"].fillna(0) * frame["quantity"]).sum()
        c1, c2, c3 = st.columns(3)
        c1.metric("Inventory items", len(frame))
        c2.metric("Total cost", f"${total_cost:,.2f}")
        c3.metric("Market estimate", f"${total_market:,.2f}", f"${total_market-total_cost:,.2f}")
        display_columns = ["sku", "card_name", "set_name", "condition", "grader", "grade", "quantity", "cost", "market_price", "graded_8_price", "graded_9_price", "psa_10_price", "list_price", "status", "storage_location"]
        st.dataframe(
            frame[display_columns],
            use_container_width=True,
            hide_index=True,
            column_config={
                "graded_8_price": st.column_config.NumberColumn("Graded 8 / 8.5", format="$%.2f"),
                "graded_9_price": st.column_config.NumberColumn("Graded 9", format="$%.2f"),
                "psa_10_price": st.column_config.NumberColumn("PSA 10", format="$%.2f"),
            },
        )

        with st.expander("Refresh grading prices from SportsCardsPro"):
            st.caption(
                "Collection exports contain only the selected condition's price. Fetch the missing "
                "Graded 8/8.5, Graded 9, and PSA 10 values here. SportsCardsPro permits one request per second."
            )
            refresh_choices = {
                f"{card['sku']} — {card['card_name']}": card for card in cards if card.get("scp_id")
            }
            missing_cards = [
                card for card in cards
                if card.get("scp_id")
                and not card.get("grade_prices_refreshed")
            ]
            st.write(f"{len(missing_cards):,} inventory cards have not had grading prices fetched yet.")
            batch_size = st.number_input(
                "Automatic batch size",
                min_value=1,
                max_value=100,
                value=25,
                help="A batch of 25 takes roughly 25 seconds because of the API rate limit.",
            )

            def refresh_cards(cards_to_refresh: list[dict]) -> None:
                client = SportsCardsProClient(token)
                refresh_progress = st.progress(0, text="Refreshing grading prices...")
                try:
                    for refresh_index, card in enumerate(cards_to_refresh, start=1):
                        product_data = client.product(card["scp_id"])
                        database.update_card(card["id"], {
                            **inventory_grade_prices(product_data),
                            "grade_prices_refreshed": 1,
                        })
                        refresh_progress.progress(
                            refresh_index / len(cards_to_refresh),
                            text=f"Refreshed {refresh_index} of {len(cards_to_refresh)} cards",
                        )
                    st.session_state.price_refresh_message = (
                        f"Updated grading prices for {len(cards_to_refresh)} cards."
                    )
                    st.rerun()
                except (SportsCardsProError, ValueError) as exc:
                    st.error(str(exc))
                finally:
                    refresh_progress.empty()

            if st.button(
                "Refresh next missing-price batch",
                disabled=not (token and missing_cards),
                type="primary",
            ):
                refresh_cards(missing_cards[: int(batch_size)])

            selected_refresh = st.multiselect("Cards to refresh", refresh_choices)
            if st.button("Refresh selected prices", disabled=not (token and selected_refresh)):
                if len(selected_refresh) > 25:
                    st.error("Choose no more than 25 cards per refresh.")
                else:
                    refresh_cards([refresh_choices[label] for label in selected_refresh])
        st.download_button(
            "Export inventory CSV",
            frame.to_csv(index=False).encode("utf-8"),
            file_name="price_hunter_inventory.csv",
            mime="text/csv",
        )

with listing_tab:
    cards = database.all_cards()
    if not cards:
        st.info("Add a card before creating a listing.")
    else:
        choices = {f"{card['sku']} — {card['card_name']}": card for card in cards}
        label = st.selectbox("Inventory item", choices)
        card = choices[label]
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
            if st.form_submit_button("Save listing draft"):
                database.update_card(card["id"], {
                    "listing_title": title.strip(),
                    "listing_description": description.strip(),
                    "list_price": float(list_price),
                    "status": status,
                    "ebay_item_id": ebay_item_id.strip(),
                })
                st.success("Listing draft saved.")
