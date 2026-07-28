"""Streamlit pages for purchases, allocations, expenses, sales, and receipts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

import database
from grading import grading_opportunity
from sales import packing_slip_text
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
    with st.expander("Record a multi-card or lot sale"):
        available_cards = [
            card for card in database.all_cards() if int(card.get("quantity") or 0) > 0
        ]
        if not available_cards:
            st.info("No inventory is available for a lot sale.")
        else:
            card_choices = {
                f"{card['sku']} — {card['card_name']}": card for card in available_cards
            }
            selected_labels = st.multiselect("Cards in sale", card_choices, key="lot_sale_cards")
            selected_cards = [card_choices[label] for label in selected_labels]
            lot_items = []
            lot_subtotal = 0.0
            for card in selected_cards:
                item_columns = st.columns(3)
                item_columns[0].write(f"**{card['sku']} — {card['card_name']}**")
                quantity = int(item_columns[1].number_input(
                    "Quantity",
                    min_value=1, max_value=int(card["quantity"]), value=1,
                    key=f"lot_quantity_{card['id']}",
                ))
                unit_price = float(item_columns[2].number_input(
                    "Sale price per card", min_value=0.0, format="%.2f",
                    key=f"lot_price_{card['id']}",
                ))
                lot_items.append({
                    "card_id": int(card["id"]),
                    "quantity": quantity,
                    "unit_price": unit_price,
                })
                lot_subtotal += quantity * unit_price
            first_row = st.columns(4)
            lot_date = first_row[0].date_input("Sale date", key="lot_sale_date")
            lot_marketplace = first_row[1].selectbox(
                "Marketplace", ["eBay", "Whatnot", "Facebook", "Card show", "Direct", "Other"],
                key="lot_marketplace",
            )
            lot_buyer = first_row[2].text_input("Buyer", key="lot_buyer")
            lot_order = first_row[3].text_input("Order number", key="lot_order")
            second_row = st.columns(4)
            lot_shipping = second_row[0].number_input(
                "Shipping charged", min_value=0.0, format="%.2f", key="lot_shipping"
            )
            lot_fees = second_row[1].number_input(
                "Platform fees", min_value=0.0, format="%.2f", key="lot_fees"
            )
            lot_promoted = second_row[2].number_input(
                "Promoted fees", min_value=0.0, format="%.2f", key="lot_promoted"
            )
            lot_label = second_row[3].number_input(
                "Shipping label", min_value=0.0, format="%.2f", key="lot_label"
            )
            lot_notes = st.text_area("Notes", key="lot_notes")
            st.caption(f"Item subtotal: ${lot_subtotal:,.2f}")
            lot_confirm = st.checkbox(
                "Confirm sale and reduce each card’s inventory",
                key="lot_confirm",
            )
            if st.button(
                "Complete lot sale", disabled=not (lot_items and lot_confirm),
                type="primary", key="lot_submit",
            ):
                database.create_multi_card_sale(
                    {
                        "sale_date": lot_date.isoformat(),
                        "marketplace": lot_marketplace,
                        "order_number": lot_order.strip(),
                        "buyer": lot_buyer.strip(),
                        "shipping_charged": round(lot_shipping, 2),
                        "fees": round(lot_fees, 2),
                        "promoted_listing_fees": round(lot_promoted, 2),
                        "shipping_label_cost": round(lot_label, 2),
                        "status": "Completed",
                        "notes": lot_notes.strip(),
                    },
                    lot_items,
                )
                st.success("Lot sale recorded and inventory updated.")
                st.rerun()
    automatic_match = database.auto_match_ebay_sales()
    sales = database.all_sales()
    if not sales:
        st.info("No sales have been imported yet.")
        return
    if automatic_match["matched"]:
        st.success(
            f"Automatically matched {automatic_match['matched']:,} new eBay sale(s) "
            "to inventory by item ID or SKU."
        )
    frame = pd.DataFrame(sales)
    frame["profit_calculated"] = frame["profit"].notna()
    monetary_columns = [
        "item_subtotal", "shipping_charged", "fees", "promoted_listing_fees",
        "shipping_label_cost", "refunded_amount", "additional_expenses",
        "return_shipping_cost", "net_amount", "cost_of_goods", "profit",
    ]
    for column in monetary_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    frame["sale_datetime"] = pd.to_datetime(frame["sale_date"], errors="coerce")

    with st.expander("Dashboard filters", expanded=True):
        filter_columns = st.columns(3)
        valid_dates = frame["sale_datetime"].dropna()
        minimum_date = valid_dates.min().date() if not valid_dates.empty else pd.Timestamp.today().date()
        maximum_date = valid_dates.max().date() if not valid_dates.empty else pd.Timestamp.today().date()
        date_range = filter_columns[0].date_input(
            "Sale date range",
            value=(minimum_date, maximum_date),
            key="sales_dashboard_dates",
        )
        marketplaces = sorted(value for value in frame["marketplace"].dropna().unique() if value)
        selected_marketplaces = filter_columns[1].multiselect(
            "Marketplace", marketplaces, default=marketplaces,
            key="sales_dashboard_marketplaces",
        )
        statuses = sorted(value for value in frame["status"].dropna().unique() if value)
        selected_statuses = filter_columns[2].multiselect(
            "Sale status", statuses, default=statuses,
            key="sales_dashboard_statuses",
        )

    dashboard_frame = frame.copy()
    if isinstance(date_range, (tuple, list)) and len(date_range) == 2:
        start_date, end_date = date_range
        dashboard_frame = dashboard_frame[
            dashboard_frame["sale_datetime"].dt.date.between(start_date, end_date)
        ]
    if selected_marketplaces:
        dashboard_frame = dashboard_frame[
            dashboard_frame["marketplace"].isin(selected_marketplaces)
        ]
    else:
        dashboard_frame = dashboard_frame.iloc[0:0]
    if selected_statuses:
        dashboard_frame = dashboard_frame[dashboard_frame["status"].isin(selected_statuses)]
    else:
        dashboard_frame = dashboard_frame.iloc[0:0]

    gross_revenue = (
        dashboard_frame["item_subtotal"].sum() + dashboard_frame["shipping_charged"].sum()
    )
    refunds = dashboard_frame["refunded_amount"].sum()
    selling_costs = dashboard_frame[
        [
            "fees", "promoted_listing_fees", "shipping_label_cost",
            "additional_expenses", "return_shipping_cost",
        ]
    ].sum().sum()
    net_proceeds = dashboard_frame["net_amount"].sum()
    cost_of_goods = dashboard_frame["cost_of_goods"].sum()
    profit = dashboard_frame["profit"].sum()
    margin = profit / gross_revenue if gross_revenue else 0
    average_order = gross_revenue / len(dashboard_frame) if len(dashboard_frame) else 0
    matched_count = int(dashboard_frame["profit_calculated"].sum())

    metric_row_1 = st.columns(4)
    metric_row_1[0].metric("Orders", f"{len(dashboard_frame):,}")
    metric_row_1[1].metric("Gross revenue", f"${gross_revenue:,.2f}")
    metric_row_1[2].metric("Net proceeds", f"${net_proceeds:,.2f}")
    metric_row_1[3].metric("Profit", f"${profit:,.2f}", f"{margin:.1%} margin")
    metric_row_2 = st.columns(4)
    metric_row_2[0].metric("Refunds", f"${refunds:,.2f}")
    metric_row_2[1].metric("Selling and shipping costs", f"${selling_costs:,.2f}")
    metric_row_2[2].metric("Cost of goods", f"${cost_of_goods:,.2f}")
    metric_row_2[3].metric("Average order", f"${average_order:,.2f}", f"{matched_count:,} profit-calculated")

    if dashboard_frame.empty:
        st.info("No sales match the current dashboard filters.")
    else:
        dashboard_frame["sale_month"] = dashboard_frame["sale_datetime"].dt.to_period("M").astype(str)
        monthly = (
            dashboard_frame.groupby("sale_month", dropna=False)
            .agg(
                Gross_revenue=("item_subtotal", "sum"),
                Shipping_charged=("shipping_charged", "sum"),
                Refunds=("refunded_amount", "sum"),
                Net=("net_amount", "sum"),
                Cost=("cost_of_goods", "sum"),
                Profit=("profit", "sum"),
            )
            .reset_index()
        )
        monthly["Gross revenue"] = monthly["Gross_revenue"] + monthly["Shipping_charged"]
        analysis_columns = st.columns(2)
        with analysis_columns[0]:
            st.markdown("#### Monthly revenue, net, and profit")
            st.bar_chart(monthly.set_index("sale_month")[["Gross revenue", "Net", "Profit"]])
        with analysis_columns[1]:
            st.markdown("#### Monthly refunds and COGS")
            st.bar_chart(monthly.set_index("sale_month")[["Refunds", "Cost"]])

        marketplace_summary = (
            dashboard_frame.groupby("marketplace", dropna=False)
            .agg(
                Orders=("id", "count"),
                Revenue=("item_subtotal", "sum"),
                Net=("net_amount", "sum"),
                Profit=("profit", "sum"),
            )
            .reset_index()
        )
        marketplace_summary["Margin %"] = marketplace_summary.apply(
            lambda row: row["Profit"] / row["Revenue"] * 100 if row["Revenue"] else 0,
            axis=1,
        )
        breakdown_columns = st.columns(2)
        with breakdown_columns[0]:
            st.markdown("#### Marketplace performance")
            st.dataframe(
                marketplace_summary,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Revenue": st.column_config.NumberColumn(format="$%.2f"),
                    "Net": st.column_config.NumberColumn(format="$%.2f"),
                    "Profit": st.column_config.NumberColumn(format="$%.2f"),
                    "Margin %": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )
        with breakdown_columns[1]:
            st.markdown("#### Sale status")
            status_summary = (
                dashboard_frame.groupby("status", dropna=False)
                .agg(Orders=("id", "count"), Net=("net_amount", "sum"), Profit=("profit", "sum"))
                .reset_index()
            )
            st.dataframe(
                status_summary,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Net": st.column_config.NumberColumn(format="$%.2f"),
                    "Profit": st.column_config.NumberColumn(format="$%.2f"),
                },
            )

        st.markdown("#### Most and least profitable sales")
        ranked_sales = dashboard_frame[
            ["sale_date", "marketplace", "order_number", "title", "net_amount", "cost_of_goods", "profit"]
        ].sort_values("profit", ascending=False)
        rank_columns = st.columns(2)
        rank_columns[0].dataframe(
            ranked_sales.head(10), use_container_width=True, hide_index=True
        )
        rank_columns[1].dataframe(
            ranked_sales.tail(10).sort_values("profit"), use_container_width=True, hide_index=True
        )

    with st.expander("Automatic matching and unmatched sales"):
        st.caption(
            "Automatic matching runs whenever this page loads. It uses a unique eBay item ID "
            "first, then the eBay Custom label / Price Hunter SKU. Automatic matching calculates "
            "COGS and profit but does not reduce inventory."
        )
        unmatched = [sale for sale in sales if sale.get("card_id") is None]
        ebay_unmatched = [sale for sale in unmatched if sale.get("marketplace") == "eBay"]
        matched_total = len(sales) - len(unmatched)
        match_metrics = st.columns(3)
        match_metrics[0].metric("Matched sales", f"{matched_total:,}")
        match_metrics[1].metric("Unmatched eBay sales", f"{len(ebay_unmatched):,}")
        match_metrics[2].metric("Ambiguous matches", f"{automatic_match['ambiguous']:,}")
        if ebay_unmatched:
            st.dataframe(
                pd.DataFrame(ebay_unmatched)[
                    ["sale_date", "order_number", "item_id", "sku", "title", "quantity"]
                ],
                use_container_width=True,
                hide_index=True,
            )
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

    with st.expander("Manage return, refund, cancellation, or packing slip"):
        sale_choices = {
            f"#{sale['id']} {sale['sale_date']} — {sale['title']} ({sale['status']})": sale
            for sale in sales
        }
        selected_sale_label = st.selectbox("Sale", sale_choices, key="manage_sale")
        selected_sale = sale_choices[selected_sale_label]
        selected_sale_items = database.sale_items(int(selected_sale["id"]))
        if selected_sale_items:
            st.dataframe(
                pd.DataFrame(selected_sale_items)[
                    ["card_sku", "card_name", "quantity", "unit_price", "unit_cost"]
                ],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "unit_price": st.column_config.NumberColumn("Unit price", format="$%.2f"),
                    "unit_cost": st.column_config.NumberColumn("Unit cost", format="$%.2f"),
                },
            )
        st.download_button(
            "Download packing slip",
            packing_slip_text(selected_sale, selected_sale_items).encode("utf-8"),
            file_name=f"packing-slip-{selected_sale['order_number'] or selected_sale['id']}.txt",
            mime="text/plain",
        )
        adjustment_columns = st.columns(4)
        sale_statuses = ["Completed", "Partially Refunded", "Returned", "Cancelled"]
        adjusted_status = adjustment_columns[0].selectbox(
            "Sale status",
            sale_statuses,
            index=sale_statuses.index(selected_sale["status"])
            if selected_sale["status"] in sale_statuses else 0,
            key=f"adjust_status_{selected_sale['id']}",
        )
        refunded_amount = adjustment_columns[1].number_input(
            "Refunded amount", min_value=0.0,
            value=float(selected_sale.get("refunded_amount") or 0), format="%.2f",
            key=f"adjust_refund_{selected_sale['id']}",
        )
        additional_expenses = adjustment_columns[2].number_input(
            "Additional expenses", min_value=0.0,
            value=float(selected_sale.get("additional_expenses") or 0), format="%.2f",
            key=f"adjust_expenses_{selected_sale['id']}",
        )
        return_shipping = adjustment_columns[3].number_input(
            "Return shipping", min_value=0.0,
            value=float(selected_sale.get("return_shipping_cost") or 0), format="%.2f",
            key=f"adjust_return_shipping_{selected_sale['id']}",
        )
        adjustment_notes = st.text_area(
            "Sale/return notes", value=selected_sale.get("notes") or "",
            key=f"adjust_notes_{selected_sale['id']}",
        )
        can_restore = (
            adjusted_status in {"Returned", "Cancelled"}
            and not bool(selected_sale.get("inventory_restored"))
        )
        restore_inventory = st.checkbox(
            "Restore all sale items to inventory",
            value=can_restore,
            disabled=not can_restore,
            help="Inventory can only be restored once for a sale.",
            key=f"adjust_restore_{selected_sale['id']}",
        )
        if selected_sale.get("inventory_restored"):
            st.info("Inventory for this sale has already been restored.")
        gross_net = float(selected_sale.get("gross_net_amount") or selected_sale["net_amount"] or 0)
        adjusted_net = gross_net - refunded_amount - additional_expenses - return_shipping
        adjusted_cogs = (
            0.0 if restore_inventory or bool(selected_sale.get("inventory_restored"))
            else float(selected_sale.get("cost_of_goods") or 0)
        )
        adjusted_profit = adjusted_net - adjusted_cogs
        st.caption(
            f"Adjusted net: ${adjusted_net:,.2f} · Adjusted profit: ${adjusted_profit:,.2f}"
        )
        if st.button("Save sale adjustment", type="primary"):
            database.update_sale_adjustment(
                int(selected_sale["id"]),
                adjusted_status,
                refunded_amount,
                additional_expenses,
                return_shipping,
                adjustment_notes.strip(),
                restore_inventory,
            )
            st.success("Sale adjustment saved.")
            st.rerun()
    st.dataframe(
        frame[
            [
                "sale_date", "marketplace", "order_number", "item_id", "sku", "title", "quantity",
                "item_subtotal", "shipping_charged", "fees", "shipping_label_cost", "net_amount",
                "refunded_amount", "additional_expenses", "return_shipping_cost",
                "cost_of_goods", "profit", "status", "inventory_restored", "card_id",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            column: st.column_config.NumberColumn(column.replace("_", " ").title(), format="$%.2f")
            for column in [
                "item_subtotal", "shipping_charged", "fees", "shipping_label_cost",
                "net_amount", "refunded_amount", "additional_expenses", "return_shipping_cost",
                "cost_of_goods", "profit"
            ]
        },
    )


def render_grading() -> None:
    st.subheader("Grading")
    cards = [card for card in database.all_cards() if int(card["quantity"]) > 0]
    st.markdown("#### Grading opportunity report")
    st.caption(
        "Ranks ungraded inventory by probability-weighted profit. Estimates are planning tools, "
        "not guaranteed grades or sale prices."
    )
    settings = st.columns(5)
    grading_cost = settings[0].number_input(
        "Estimated cost per card", min_value=0.0, value=30.0, format="%.2f",
        key="opportunity_grading_cost",
    )
    selling_cost_percent = settings[1].number_input(
        "Selling costs %", min_value=0.0, max_value=100.0, value=15.0, format="%.1f",
        key="opportunity_selling_cost",
    )
    probability_8 = settings[2].number_input(
        "Chance of 8/8.5 %", min_value=0.0, max_value=100.0, value=20.0, format="%.1f",
        key="opportunity_probability_8",
    )
    probability_9 = settings[3].number_input(
        "Chance of 9 %", min_value=0.0, max_value=100.0, value=50.0, format="%.1f",
        key="opportunity_probability_9",
    )
    probability_10 = settings[4].number_input(
        "Chance of 10 %", min_value=0.0, max_value=100.0, value=30.0, format="%.1f",
        key="opportunity_probability_10",
    )
    probability_total = probability_8 + probability_9 + probability_10
    candidates = [
        card for card in cards
        if card.get("condition") != "Graded"
        and any(card.get(column) for column in ("graded_8_price", "graded_9_price", "psa_10_price"))
    ]
    if abs(probability_total - 100) > 0.01:
        st.warning(f"Grade probabilities must total 100%; they currently total {probability_total:.1f}%.")
    elif not candidates:
        st.info("No ungraded cards with fetched graded prices are available for analysis.")
    else:
        opportunity_rows = [
            grading_opportunity(
                card,
                grading_cost,
                selling_cost_percent / 100,
                {
                    "8 / 8.5": probability_8 / 100,
                    "9": probability_9 / 100,
                    "10": probability_10 / 100,
                },
            )
            for card in candidates
        ]
        opportunity_frame = pd.DataFrame(opportunity_rows).sort_values(
            ["expected_profit", "psa_10_profit"], ascending=False
        )
        profitable_only = st.checkbox(
            "Show only positive expected profit", value=True, key="opportunity_profitable_only"
        )
        if profitable_only:
            opportunity_frame = opportunity_frame[opportunity_frame["expected_profit"] > 0]
        if opportunity_frame.empty:
            st.info("No cards meet the current expected-profit filter.")
        else:
            top = opportunity_frame.iloc[0]
            metrics = st.columns(3)
            metrics[0].metric("Cards analyzed", f"{len(opportunity_frame):,}")
            metrics[1].metric("Best candidate", top["card_name"])
            metrics[2].metric("Best expected profit", f"${top['expected_profit']:,.2f}")
            money_columns = [
                "cost_basis", "raw_value", "grade_8_value", "grade_9_value", "psa_10_value",
                "grading_cost", "grade_8_profit", "grade_9_profit", "psa_10_profit",
                "expected_value", "expected_profit", "expected_uplift_vs_raw",
            ]
            st.dataframe(
                opportunity_frame,
                use_container_width=True,
                hide_index=True,
                column_config={
                    column: st.column_config.NumberColumn(
                        column.replace("_", " ").title(), format="$%.2f"
                    )
                    for column in money_columns
                },
            )
            st.download_button(
                "Export grading opportunities CSV",
                opportunity_frame.to_csv(index=False).encode("utf-8"),
                file_name="price_hunter_grading_opportunities.csv",
                mime="text/csv",
            )

    with st.expander("Create grading submission", expanded=not database.all_grading_submissions()):
        if not cards:
            st.info("Add inventory before creating a grading submission.")
        else:
            choices = {f"{card['sku']} — {card['card_name']}": card for card in cards}
            selected_labels = st.multiselect("Cards", choices, key="grading_new_cards")
            selected = [choices[label] for label in selected_labels]
            left, right = st.columns(2)
            with left:
                grader = st.selectbox("Grading company", ["PSA", "SGC", "BGS", "CGC", "Other"])
                submission_number = st.text_input("Submission number")
                grading_status = st.selectbox(
                    "Submission status", ["Preparing", "Submitted", "Grading"]
                )
                submitted_date = st.date_input("Submission date", value=None)
            with right:
                grading_fee = st.number_input("Total grading fees", min_value=0.0, format="%.2f")
                shipping_cost = st.number_input("Shipping", min_value=0.0, format="%.2f")
                insurance_cost = st.number_input("Insurance", min_value=0.0, format="%.2f")
                other_cost = st.number_input("Other costs", min_value=0.0, format="%.2f")
            quantities = []
            for card in selected:
                quantities.append({
                    "card_id": int(card["id"]),
                    "quantity": int(st.number_input(
                        f"Quantity — {card['sku']} {card['card_name']}",
                        min_value=1, max_value=int(card["quantity"]), value=1,
                        key=f"grade_quantity_{card['id']}",
                    )),
                })
            total_cost = grading_fee + shipping_cost + insurance_cost + other_cost
            total_units = sum(item["quantity"] for item in quantities)
            if total_units:
                st.caption(
                    f"{total_units} card(s); ${total_cost:,.2f} total grading cost; "
                    f"approximately ${total_cost / total_units:,.2f} per card."
                )
            notes = st.text_area("Submission notes")
            if st.button("Create submission", disabled=not selected, type="primary"):
                database.create_grading_submission(
                    {
                        "submission_number": submission_number.strip(),
                        "grader": grader,
                        "status": grading_status,
                        "submitted_date": submitted_date.isoformat() if submitted_date else None,
                        "grading_fee": round(grading_fee, 2),
                        "shipping_cost": round(shipping_cost, 2),
                        "insurance_cost": round(insurance_cost, 2),
                        "other_cost": round(other_cost, 2),
                        "notes": notes.strip(),
                    },
                    quantities,
                )
                st.success("Grading submission created.")
                st.rerun()

    submissions = database.all_grading_submissions()
    if not submissions:
        st.info("No grading submissions yet.")
        return
    st.dataframe(
        pd.DataFrame(submissions),
        use_container_width=True,
        hide_index=True,
        column_config={
            column: st.column_config.NumberColumn(column.replace("_", " ").title(), format="$%.2f")
            for column in ["grading_fee", "shipping_cost", "insurance_cost", "other_cost"]
        },
    )
    submission_choices = {
        f"#{row['id']} {row['grader']} — {row['submission_number'] or 'No number'} ({row['status']})": row
        for row in submissions
    }
    selected_label = st.selectbox("Manage submission", submission_choices)
    submission = submission_choices[selected_label]
    items = database.grading_items(int(submission["id"]))
    status_options = ["Preparing", "Submitted", "Grading", "Returned", "Rejected"]
    new_status = st.selectbox(
        "Current status", status_options,
        index=status_options.index(submission["status"]) if submission["status"] in status_options else 0,
        key=f"submission_status_{submission['id']}",
    )
    returned_date = st.date_input(
        "Return date", value=None, key=f"submission_returned_{submission['id']}"
    )
    results = []
    for item in items:
        st.markdown(f"**{item.get('sku', '')} — {item.get('card_name', 'Deleted card')}**")
        result_columns = st.columns(3)
        grade = result_columns[0].text_input(
            "Grade", value=item["grade"], key=f"result_grade_{item['id']}"
        )
        certification = result_columns[1].text_input(
            "Certification number", value=item["certification_number"],
            key=f"result_cert_{item['id']}",
        )
        result_columns[2].metric("Allocated grading cost", f"${item['allocated_grading_cost']:,.2f}")
        results.append({
            "item_id": int(item["id"]),
            "grade": grade.strip(),
            "certification_number": certification.strip(),
        })
    if st.button("Save submission progress", type="primary"):
        if new_status == "Returned" and any(not result["grade"] for result in results):
            st.error("Enter a grade for every returned card.")
        else:
            database.update_grading_submission(
                int(submission["id"]),
                new_status,
                returned_date.isoformat() if returned_date else None,
                results,
            )
            st.success("Grading submission updated.")
            st.rerun()


def render_inventory_history() -> None:
    st.subheader("Inventory history")
    cards = database.all_cards()
    choices = {"All cards": None}
    choices.update({f"{card['sku']} — {card['card_name']}": int(card["id"]) for card in cards})
    selected = st.selectbox("Card", choices)
    events = database.inventory_events(choices[selected])
    if not events:
        st.info("No recorded activity yet. New inventory changes will appear here.")
        return
    frame = pd.DataFrame(events)
    st.dataframe(
        frame[
            [
                "created_at", "card_sku", "card_name", "event_type", "details",
                "quantity_change", "amount", "reference_type", "reference_id",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={"amount": st.column_config.NumberColumn("Amount", format="$%.2f")},
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
