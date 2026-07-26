"""Streamlit pages for purchases, allocations, expenses, sales, and receipts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

import database
from business_imports import (
    equal_card_allocations,
    read_ebay_workbook,
    read_expense_workbook,
    read_purchase_workbook,
)
from sportscardspro import matches_terms


RECEIPT_DIR = Path(__file__).with_name("data") / "receipts"
RECEIPT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def _normalized_reference(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _index_receipt_folder(folder: Path) -> tuple[int, int]:
    references = [
        ("purchase", row["id"], _normalized_reference(row["receipt_ref"]))
        for row in database.all_purchases()
        if row["receipt_ref"]
    ] + [
        ("expense", row["id"], _normalized_reference(row["receipt_ref"]))
        for row in database.all_expenses()
        if row["receipt_ref"]
    ]
    indexed = 0
    matched = 0
    for file in folder.rglob("*"):
        if not file.is_file() or file.suffix.casefold() not in RECEIPT_EXTENSIONS:
            continue
        normalized_path = _normalized_reference(str(file))
        file_matches = [
            (entity_type, entity_id)
            for entity_type, entity_id, reference in references
            if len(reference) >= 4 and reference in normalized_path
        ]
        if file_matches:
            for entity_type, entity_id in file_matches:
                database.add_receipt(entity_type, entity_id, file.name, str(file))
            matched += 1
        else:
            database.add_receipt("unlinked", 0, file.name, str(file))
        indexed += 1
    return indexed, matched


def _preview_import(
    uploader_key: str,
    label: str,
    reader: Any,
    importer: Any,
    button_label: str,
) -> None:
    upload = st.file_uploader(label, type="xlsx", key=uploader_key)
    if not upload:
        return
    rows, errors = reader(upload)
    st.write(f"{len(rows):,} valid rows are ready to import.")
    if errors:
        with st.expander(f"{len(errors):,} rows require attention"):
            for error in errors[:100]:
                st.warning(error)
    if rows:
        st.dataframe(pd.DataFrame(rows[:10]), use_container_width=True, hide_index=True)
        if st.button(button_label, key=f"{uploader_key}_import", type="primary"):
            inserted = importer(rows)
            st.success(f"Imported {inserted:,} new rows; existing duplicates were skipped.")


def render_purchases() -> None:
    st.subheader("Purchases and lots")
    st.caption(
        "Import the Inventory Purchase Log from CardBusiness_Starter_Tracker.xlsx. "
        "Landed cost equals purchase price plus shipping and tax."
    )
    with st.expander("Import purchase workbook"):
        _preview_import(
            "purchase_workbook",
            "CardBusiness Starter Tracker",
            read_purchase_workbook,
            database.import_purchases,
            "Import purchases",
        )

    purchases = database.all_purchases()
    if not purchases:
        st.info("No purchases have been imported yet.")
        return
    frame = pd.DataFrame(purchases)
    total = frame["total_cost"].sum()
    allocated = int((frame["status"] == "Allocated").sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Purchases", len(frame))
    c2.metric("Landed cost", f"${total:,.2f}")
    c3.metric("Allocated", f"{allocated:,} of {len(frame):,}")
    st.dataframe(
        frame[
            [
                "purchase_date", "description", "category", "set_name", "quantity",
                "cards_expected", "vendor", "purchase_price", "shipping", "tax",
                "total_cost", "payment_account", "receipt_ref", "status",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            column: st.column_config.NumberColumn(column.replace("_", " ").title(), format="$%.2f")
            for column in ["purchase_price", "shipping", "tax", "total_cost"]
        },
    )


def render_allocation() -> None:
    st.subheader("Equal per-card cost allocation")
    purchases = database.all_purchases()
    if not purchases:
        st.info("Import purchases before allocating card costs.")
        return
    choices = {
        f"P-{purchase['id']:05d} — {purchase['description']} (${purchase['total_cost']:,.2f})": purchase
        for purchase in purchases
    }
    selected_label = st.selectbox("Purchase", choices)
    purchase = choices[selected_label]
    existing = database.purchase_allocations(purchase["id"])
    current_ids = {row["card_id"] for row in existing}
    allocated_elsewhere = database.allocated_card_ids() - current_ids

    set_filter = st.text_input(
        "Inventory set must contain these words",
        value=purchase["set_name"],
        key=f"allocation_set_{purchase['id']}",
        help="Adjust this when the spreadsheet and SportsCardsPro use different set names.",
    )
    available_cards = [
        card
        for card in database.all_cards()
        if card["id"] not in allocated_elsewhere
        and matches_terms(card.get("set_name"), set_filter)
    ]
    physical_count = sum(int(card["quantity"]) for card in available_cards)
    expected = int(purchase.get("cards_expected") or 0)
    st.write(
        f"{len(available_cards):,} inventory rows / {physical_count:,} physical cards match. "
        f"The purchase workbook expects {expected:,} cards."
    )
    select_all = st.checkbox(
        "Allocate across every matching card",
        value=True,
        key=f"allocation_all_{purchase['id']}",
    )
    card_choices = {
        f"{card['sku']} — {card['card_name']} (qty {card['quantity']})": card
        for card in available_cards
    }
    if select_all:
        selected_cards = available_cards
    else:
        selected_card_labels = st.multiselect(
            "Cards included in this purchase",
            card_choices,
            key=f"allocation_cards_{purchase['id']}",
        )
        selected_cards = [card_choices[label] for label in selected_card_labels]

    selected_units = sum(int(card["quantity"]) for card in selected_cards)
    if selected_units:
        average = purchase["total_cost"] / selected_units
        st.info(
            f"${purchase['total_cost']:,.2f} will be allocated across {selected_units:,} cards "
            f"at approximately ${average:,.2f} each."
        )
    if st.button(
        "Apply equal allocation",
        disabled=not selected_cards,
        type="primary",
        key=f"allocate_{purchase['id']}",
    ):
        allocations = equal_card_allocations(purchase["total_cost"], selected_cards)
        database.allocate_purchase(purchase["id"], allocations)
        st.success(
            f"Allocated ${sum(row['allocated_total'] for row in allocations):,.2f} "
            f"across {selected_units:,} physical cards."
        )
        st.rerun()

    if existing:
        allocation_frame = pd.DataFrame(existing)
        st.dataframe(
            allocation_frame[
                [
                    "sku", "card_name", "quantity", "base_unit_cost",
                    "higher_cost_units", "allocated_total",
                ]
            ],
            use_container_width=True,
            hide_index=True,
            column_config={
                "base_unit_cost": st.column_config.NumberColumn("Base card cost", format="$%.2f"),
                "allocated_total": st.column_config.NumberColumn("Allocated total", format="$%.2f"),
            },
        )


def render_expenses() -> None:
    st.subheader("Business expenses and supplies")
    with st.expander("Import expense workbook"):
        _preview_import(
            "expense_workbook",
            "Tax Sheet workbook",
            read_expense_workbook,
            database.import_expenses,
            "Import expenses",
        )
    expenses = database.all_expenses()
    if not expenses:
        st.info("No expenses have been imported yet.")
        return
    frame = pd.DataFrame(expenses)
    c1, c2 = st.columns(2)
    c1.metric("Expense records", len(frame))
    c2.metric("Total expenses", f"${frame['total'].sum():,.2f}")
    st.dataframe(
        frame[
            [
                "expense_date", "vendor", "description", "quantity", "category",
                "amount", "tax", "shipping", "total", "payment_account", "receipt_ref", "notes",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            column: st.column_config.NumberColumn(column.title(), format="$%.2f")
            for column in ["amount", "tax", "shipping", "total"]
        },
    )


def render_sales() -> None:
    st.subheader("Sales")
    with st.expander("Import eBay transaction report"):
        _preview_import(
            "ebay_workbook",
            "2025 eBay Sales workbook",
            read_ebay_workbook,
            database.import_sales,
            "Import eBay sales",
        )
    sales = database.all_sales()
    if not sales:
        st.info("No sales have been imported yet.")
        return
    frame = pd.DataFrame(sales)
    matched_count = int(frame["card_id"].notna().sum())
    matched_profit = frame["profit"].fillna(0).sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Orders", len(frame))
    c2.metric("Item revenue", f"${frame['item_subtotal'].sum():,.2f}")
    c3.metric("Net after eBay and labels", f"${frame['net_amount'].sum():,.2f}")
    c4.metric("Matched profit", f"${matched_profit:,.2f}", f"{matched_count:,} matched")

    dashboard_frame = frame.copy()
    dashboard_frame["sale_month"] = pd.to_datetime(
        dashboard_frame["sale_date"], errors="coerce"
    ).dt.to_period("M").astype(str)
    monthly = (
        dashboard_frame.groupby("sale_month", dropna=False)
        .agg(
            Revenue=("item_subtotal", "sum"),
            Net=("net_amount", "sum"),
            Cost=("cost_of_goods", "sum"),
            Profit=("profit", "sum"),
        )
        .reset_index()
    )
    if not monthly.empty:
        st.markdown("#### Monthly sales and profit")
        st.bar_chart(monthly.set_index("sale_month")[["Revenue", "Net", "Profit"]])

    with st.expander("Match sales to inventory"):
        if st.button("Auto-match exact eBay item IDs"):
            count = database.auto_match_sales_by_ebay_item_id()
            st.success(f"Matched {count:,} sale(s) by exact eBay item ID.")
            st.rerun()
        unmatched = [sale for sale in sales if sale.get("card_id") is None]
        cards = database.all_cards()
        if unmatched and cards:
            sale_choices = {
                f"{sale['sale_date']} — {sale['title']} ({sale['order_number']})": sale
                for sale in unmatched
            }
            card_choices = {
                f"{card['sku']} — {card['card_name']}": card for card in cards
            }
            sale_label = st.selectbox("Unmatched sale", sale_choices)
            card_label = st.selectbox("Inventory card", card_choices)
            reduce_inventory = st.checkbox(
                "Reduce inventory quantity",
                value=False,
                help="Leave off when the imported inventory already excludes sold cards.",
            )
            if st.button("Link sale to selected card"):
                database.link_sale_to_card(
                    sale_choices[sale_label]["id"],
                    card_choices[card_label]["id"],
                    reduce_inventory=reduce_inventory,
                )
                st.rerun()
    st.dataframe(
        frame[
            [
                "sale_date", "marketplace", "order_number", "item_id", "title", "quantity",
                "item_subtotal", "shipping_charged", "fees", "shipping_label_cost", "net_amount",
                "cost_of_goods", "profit", "card_id",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            column: st.column_config.NumberColumn(column.replace("_", " ").title(), format="$%.2f")
            for column in [
                "item_subtotal", "shipping_charged", "fees", "shipping_label_cost",
                "net_amount", "cost_of_goods", "profit"
            ]
        },
    )


def render_receipts() -> None:
    st.subheader("Receipts")
    purchases = database.all_purchases()
    expenses = database.all_expenses()
    reference_rows = [
        {
            "type": "Purchase",
            "id": row["id"],
            "date": row["purchase_date"],
            "vendor": row["vendor"],
            "description": row["description"],
            "receipt_reference": row["receipt_ref"],
        }
        for row in purchases
        if row["receipt_ref"]
    ] + [
        {
            "type": "Expense",
            "id": row["id"],
            "date": row["expense_date"],
            "vendor": row["vendor"],
            "description": row["description"],
            "receipt_reference": row["receipt_ref"],
        }
        for row in expenses
        if row["receipt_ref"]
    ]
    if reference_rows:
        st.dataframe(pd.DataFrame(reference_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No receipt references have been imported.")

    with st.expander("Index an existing receipt folder"):
        folder_path = st.text_input(
            "Receipt folder",
            value=r"I:\iCloudDrive\2. Finance\Receipts",
            help="Files remain in their current folder; Price Hunter stores only their paths.",
        )
        if st.button("Index receipt folder"):
            folder = Path(folder_path)
            if not folder.is_dir():
                st.error("That receipt folder could not be found.")
            else:
                indexed, matched = _index_receipt_folder(folder)
                st.success(
                    f"Indexed {indexed:,} receipt files; {matched:,} matched an imported receipt reference."
                )

    entities = {
        **{
            f"Purchase P-{row['id']:05d} — {row['description']}": ("purchase", row["id"])
            for row in purchases
        },
        **{
            f"Expense E-{row['id']:05d} — {row['description']}": ("expense", row["id"])
            for row in expenses
        },
    }
    if entities:
        st.markdown("#### Attach receipt files")
        entity_label = st.selectbox("Attach to", entities)
        uploads = st.file_uploader(
            "Receipt images or PDFs",
            type=["pdf", "png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            key="receipt_uploads",
        )
        if st.button("Save receipt files", disabled=not uploads):
            entity_type, entity_id = entities[entity_label]
            target_dir = RECEIPT_DIR / entity_type / str(entity_id)
            target_dir.mkdir(parents=True, exist_ok=True)
            for upload in uploads:
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(upload.name).name)
                target = target_dir / safe_name
                target.write_bytes(upload.getvalue())
                database.add_receipt(entity_type, entity_id, upload.name, str(target))
            st.success(f"Saved {len(uploads)} receipt file(s).")

    saved = database.all_receipts()
    if saved:
        st.markdown("#### Saved receipt files")
        st.dataframe(pd.DataFrame(saved), use_container_width=True, hide_index=True)


def render_reports() -> None:
    st.subheader("Business overview")
    purchases = pd.DataFrame(database.all_purchases())
    expenses = pd.DataFrame(database.all_expenses())
    sales = pd.DataFrame(database.all_sales())
    receipts = pd.DataFrame(database.all_receipts())

    purchase_total = purchases["total_cost"].sum() if not purchases.empty else 0.0
    expense_total = expenses["total"].sum() if not expenses.empty else 0.0
    sales_net = sales["net_amount"].sum() if not sales.empty else 0.0
    linked_receipts = (
        int((receipts["entity_type"] != "unlinked").sum()) if not receipts.empty else 0
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Inventory purchases", f"${purchase_total:,.2f}")
    c2.metric("Business expenses", f"${expense_total:,.2f}")
    c3.metric("eBay net proceeds", f"${sales_net:,.2f}")
    c4.metric("Linked receipts", f"{linked_receipts:,}")

    report_left, report_right = st.columns(2)
    with report_left:
        st.markdown("#### Purchases by set")
        if purchases.empty:
            st.info("No purchase data.")
        else:
            purchase_summary = (
                purchases.groupby("set_name", dropna=False)
                .agg(Purchases=("id", "count"), Cards=("cards_expected", "sum"), Cost=("total_cost", "sum"))
                .reset_index()
                .sort_values("Cost", ascending=False)
            )
            st.dataframe(
                purchase_summary,
                use_container_width=True,
                hide_index=True,
                column_config={"Cost": st.column_config.NumberColumn("Cost", format="$%.2f")},
            )
    with report_right:
        st.markdown("#### Expenses by category")
        if expenses.empty:
            st.info("No expense data.")
        else:
            expense_summary = (
                expenses.groupby("category", dropna=False)
                .agg(Records=("id", "count"), Total=("total", "sum"))
                .reset_index()
                .sort_values("Total", ascending=False)
            )
            st.dataframe(
                expense_summary,
                use_container_width=True,
                hide_index=True,
                column_config={"Total": st.column_config.NumberColumn("Total", format="$%.2f")},
            )
