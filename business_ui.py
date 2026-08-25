"""Streamlit pages for purchases, allocations, expenses, sales, and receipts."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

import database
from grading import PSA_SERVICE_TIERS, grading_opportunity, psa_declared_value, psa_tier_for_value
from receipt_scanner import allocate_receipt_amount, scan_receipt_image, scan_receipt_pdf
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
    file_types: str | list[str] = "xlsx",
) -> None:
    upload = st.file_uploader(label, type=file_types, key=uploader_key)
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


def _date_filtered(frame: pd.DataFrame, column: str, start: date, end: date) -> pd.DataFrame:
    if frame.empty or column not in frame:
        return frame
    dated = frame.copy()
    dated["_report_date"] = pd.to_datetime(dated[column], errors="coerce")
    return dated[dated["_report_date"].dt.date.between(start, end)]


def tax_summary_rows(
    purchases: pd.DataFrame,
    expenses: pd.DataFrame,
    sales: pd.DataFrame,
) -> list[dict[str, Any]]:
    sale_value = lambda column: sales[column].sum() if not sales.empty and column in sales else 0.0
    purchase_value = lambda column: purchases[column].sum() if not purchases.empty and column in purchases else 0.0
    expense_value = lambda column: expenses[column].sum() if not expenses.empty and column in expenses else 0.0

    gross_sales = sale_value("item_subtotal")
    shipping_income = sale_value("shipping_charged")
    refunds = sale_value("refunded_amount")
    tax_collected = sale_value("tax_collected")
    gross_receipts = gross_sales + shipping_income - refunds
    cogs = sale_value("cost_of_goods")
    marketplace_fees = sale_value("fees")
    promoted_fees = sale_value("promoted_listing_fees")
    shipping_labels = sale_value("shipping_label_cost")
    return_shipping = sale_value("return_shipping_cost")
    additional_sale_expenses = sale_value("additional_expenses")
    business_expenses = expense_value("total")
    estimated_net = (
        gross_receipts
        - cogs
        - marketplace_fees
        - promoted_fees
        - shipping_labels
        - return_shipping
        - additional_sale_expenses
        - business_expenses
    )

    return [
        {"Section": "Sales", "Line Item": "Gross card sales", "Amount": round(gross_sales, 2)},
        {"Section": "Sales", "Line Item": "Shipping charged to buyers", "Amount": round(shipping_income, 2)},
        {"Section": "Sales", "Line Item": "Refunds and returns", "Amount": round(-refunds, 2)},
        {"Section": "Sales", "Line Item": "Tax collected from buyers", "Amount": round(tax_collected, 2)},
        {"Section": "Sales", "Line Item": "Gross receipts before sales tax", "Amount": round(gross_receipts, 2)},
        {"Section": "Cost of goods sold", "Line Item": "COGS from matched sales", "Amount": round(cogs, 2)},
        {"Section": "Cost of goods sold", "Line Item": "Gross profit after COGS", "Amount": round(gross_receipts - cogs, 2)},
        {"Section": "Selling costs", "Line Item": "Marketplace fees", "Amount": round(marketplace_fees, 2)},
        {"Section": "Selling costs", "Line Item": "Promoted listing fees", "Amount": round(promoted_fees, 2)},
        {"Section": "Selling costs", "Line Item": "Shipping labels", "Amount": round(shipping_labels, 2)},
        {"Section": "Selling costs", "Line Item": "Return shipping", "Amount": round(return_shipping, 2)},
        {"Section": "Selling costs", "Line Item": "Additional sale expenses", "Amount": round(additional_sale_expenses, 2)},
        {"Section": "Operating expenses", "Line Item": "Business expenses", "Amount": round(business_expenses, 2)},
        {"Section": "Planning total", "Line Item": "Estimated net before owner tax review", "Amount": round(estimated_net, 2)},
        {"Section": "Inventory cash flow", "Line Item": "Inventory purchases paid", "Amount": round(purchase_value("total_cost"), 2)},
        {"Section": "Inventory cash flow", "Line Item": "Inventory purchase tax", "Amount": round(purchase_value("tax"), 2)},
        {"Section": "Inventory cash flow", "Line Item": "Inventory purchase shipping", "Amount": round(purchase_value("shipping"), 2)},
    ]


def render_tax_summary(
    purchases: pd.DataFrame,
    expenses: pd.DataFrame,
    sales: pd.DataFrame,
) -> None:
    st.markdown("#### Tax prep summary")
    st.caption(
        "Planning view only. Inventory purchases are shown as cash spent separately from COGS "
        "because tax treatment depends on your accounting method and year-end inventory."
    )
    current_year = pd.Timestamp.today().year
    years = set()
    for frame, column in ((purchases, "purchase_date"), (expenses, "expense_date"), (sales, "sale_date")):
        if not frame.empty and column in frame:
            dates = pd.to_datetime(frame[column], errors="coerce").dropna()
            years.update(int(value.year) for value in dates)
    year_options = sorted(years or {current_year}, reverse=True)
    selected_year = st.selectbox("Tax year", year_options, index=0, key="tax_summary_year")
    start_date = date(selected_year, 1, 1)
    end_date = date(selected_year, 12, 31)

    filtered_purchases = _date_filtered(purchases, "purchase_date", start_date, end_date)
    filtered_expenses = _date_filtered(expenses, "expense_date", start_date, end_date)
    filtered_sales = _date_filtered(sales, "sale_date", start_date, end_date)
    summary = pd.DataFrame(tax_summary_rows(filtered_purchases, filtered_expenses, filtered_sales))
    summary_pivot = (
        summary.pivot_table(index="Section", values="Amount", aggfunc="sum")
        .reset_index()
        .rename(columns={"Amount": "Section Total"})
    )

    key_amounts = {row["Line Item"]: row["Amount"] for row in summary.to_dict("records")}
    metrics = st.columns(4)
    metrics[0].metric("Gross receipts", f"${key_amounts['Gross receipts before sales tax']:,.2f}")
    metrics[1].metric("COGS", f"${key_amounts['COGS from matched sales']:,.2f}")
    metrics[2].metric("Business expenses", f"${key_amounts['Business expenses']:,.2f}")
    metrics[3].metric("Estimated net", f"${key_amounts['Estimated net before owner tax review']:,.2f}")

    st.dataframe(
        summary,
        use_container_width=True,
        hide_index=True,
        column_config={"Amount": st.column_config.NumberColumn("Amount", format="$%.2f")},
    )
    st.dataframe(
        summary_pivot,
        use_container_width=True,
        hide_index=True,
        column_config={"Section Total": st.column_config.NumberColumn("Section Total", format="$%.2f")},
    )

    if not filtered_expenses.empty:
        expense_summary = (
            filtered_expenses.groupby("category", dropna=False)
            .agg(Records=("id", "count"), Total=("total", "sum"))
            .reset_index()
            .sort_values("Total", ascending=False)
        )
        st.markdown("#### Expense category detail")
        st.dataframe(
            expense_summary,
            use_container_width=True,
            hide_index=True,
            column_config={"Total": st.column_config.NumberColumn("Total", format="$%.2f")},
        )

    export_columns = st.columns(3)
    export_columns[0].download_button(
        "Download tax summary CSV",
        summary.to_csv(index=False).encode("utf-8"),
        file_name=f"price_hunter_tax_summary_{selected_year}.csv",
        mime="text/csv",
    )
    export_columns[1].download_button(
        "Download sales detail CSV",
        filtered_sales.to_csv(index=False).encode("utf-8"),
        file_name=f"price_hunter_tax_sales_{selected_year}.csv",
        mime="text/csv",
        disabled=filtered_sales.empty,
    )
    export_columns[2].download_button(
        "Download expenses detail CSV",
        filtered_expenses.to_csv(index=False).encode("utf-8"),
        file_name=f"price_hunter_tax_expenses_{selected_year}.csv",
        mime="text/csv",
        disabled=filtered_expenses.empty,
    )


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
    with st.expander("Scan purchase receipt", expanded=True):
        purchase_receipt_upload = st.file_uploader(
            "Purchase receipt PDF or photo",
            type=["pdf", "jpg", "jpeg", "png", "webp", "heic", "heif"],
            key="purchase_receipt_scanner",
        )
        st.caption(
            "Supports text PDFs and receipt photos, including iPhone HEIC. "
            "Review every detected value before adding the purchase."
        )
        if st.button(
            "Scan and prefill purchase", disabled=purchase_receipt_upload is None,
            key="scan_purchase_receipt",
        ):
            try:
                if purchase_receipt_upload.name.casefold().endswith(".pdf"):
                    scanned_purchase = scan_receipt_pdf(
                        purchase_receipt_upload.getvalue(), purchase_receipt_upload.name
                    )
                else:
                    scanned_purchase = scan_receipt_image(
                        purchase_receipt_upload.getvalue(), purchase_receipt_upload.name
                    )
                if scanned_purchase.get("expense_date"):
                    scanned_date = pd.to_datetime(scanned_purchase["expense_date"], errors="coerce")
                    if not pd.isna(scanned_date):
                        st.session_state.purchase_add_date = scanned_date.date()
                purchase_prefill = {
                    "vendor": scanned_purchase["vendor"],
                    "description": scanned_purchase["description"],
                    "category": scanned_purchase["category"],
                    "quantity": max(
                        1,
                        sum(
                            int(item.get("quantity") or 1)
                            for item in scanned_purchase.get("line_items", [])
                        ) or int(scanned_purchase["quantity"]),
                    ),
                    "purchase_price": round(
                        float(scanned_purchase["amount"]) * int(scanned_purchase["quantity"]), 2
                    ),
                    "shipping": float(scanned_purchase["shipping"]),
                    "tax": float(scanned_purchase["tax"]),
                    "payment_account": scanned_purchase["payment_account"],
                    "receipt_ref": scanned_purchase["receipt_ref"],
                    "notes": scanned_purchase["notes"],
                }
                for field, value in purchase_prefill.items():
                    st.session_state[f"purchase_add_{field}"] = value
                st.session_state.purchase_scan_items = scanned_purchase.get("line_items", [])
                line_items = scanned_purchase.get("line_items", [])
                if line_items:
                    line_totals = [float(item["total"]) for item in line_items]
                    allocated_shipping = allocate_receipt_amount(
                        float(scanned_purchase["shipping"]), line_totals
                    )
                    allocated_tax = allocate_receipt_amount(float(scanned_purchase["tax"]), line_totals)
                    scanned_rows = []
                    for index, item in enumerate(line_items):
                        identity = "|".join((
                            str(scanned_purchase["receipt_ref"]), str(scanned_purchase["vendor"]),
                            str(scanned_purchase["expense_date"]), str(index), str(item["description"]),
                            str(item["total"]),
                        ))
                        line_total = round(float(item["total"]), 2)
                        scanned_rows.append({
                            "source_key": "receipt:" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                            "purchase_date": scanned_purchase["expense_date"],
                            "description": str(item["description"]),
                            "category": scanned_purchase["category"],
                            "set_name": "",
                            "quantity": int(item["quantity"]),
                            "cards_expected": 0,
                            "vendor": scanned_purchase["vendor"],
                            "purchase_price": line_total,
                            "shipping": allocated_shipping[index],
                            "tax": allocated_tax[index],
                            "total_cost": round(
                                line_total + allocated_shipping[index] + allocated_tax[index], 2
                            ),
                            "payment_account": scanned_purchase["payment_account"],
                            "receipt_ref": scanned_purchase["receipt_ref"],
                            "notes": (
                                f"{scanned_purchase['notes']}; detected unit price "
                                f"${float(item['unit_price']):,.2f}"
                            ),
                            "status": "Unallocated",
                        })
                    st.session_state.purchase_scan_rows = scanned_rows
                else:
                    st.session_state.purchase_scan_rows = []
                editor_identity = (
                    f"{purchase_receipt_upload.name}|{scanned_purchase['receipt_ref']}|"
                    f"{scanned_purchase['total']}"
                )
                st.session_state.purchase_scan_editor_token = hashlib.sha256(
                    editor_identity.encode("utf-8")
                ).hexdigest()[:12]
                st.session_state.purchase_scan_message = (
                    f"Detected ${scanned_purchase['total']:,.2f} from "
                    f"{purchase_receipt_upload.name}. Review the prefilled form below."
                )
                st.rerun()
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        if st.session_state.get("purchase_scan_message"):
            st.success(st.session_state.purchase_scan_message)
        if st.session_state.get("purchase_scan_items"):
            st.markdown("#### Detected line items")
            scanned_purchase_rows = st.session_state.get("purchase_scan_rows", [])
            edited_purchase_lines = st.data_editor(
                pd.DataFrame(scanned_purchase_rows)[
                    ["description", "quantity", "purchase_price", "shipping", "tax"]
                ],
                use_container_width=True, hide_index=True, num_rows="dynamic",
                key=(
                    "purchase_scan_line_editor_"
                    + st.session_state.get("purchase_scan_editor_token", "current")
                ),
                column_config={
                    "description": st.column_config.TextColumn("Description", required=True),
                    "quantity": st.column_config.NumberColumn(
                        "Quantity", min_value=1, step=1, required=True
                    ),
                    "purchase_price": st.column_config.NumberColumn(
                        "Line price", min_value=0.0, format="$%.2f", required=True
                    ),
                    "shipping": st.column_config.NumberColumn(
                        "Allocated shipping", min_value=0.0, format="$%.2f", required=True
                    ),
                    "tax": st.column_config.NumberColumn(
                        "Allocated tax", min_value=0.0, format="$%.2f", required=True
                    ),
                },
            )
            edited_landed_total = (
                pd.to_numeric(edited_purchase_lines["purchase_price"], errors="coerce").fillna(0)
                + pd.to_numeric(edited_purchase_lines["shipping"], errors="coerce").fillna(0)
                + pd.to_numeric(edited_purchase_lines["tax"], errors="coerce").fillna(0)
            ).sum()
            st.caption(
                f"Editable landed-cost total: ${edited_landed_total:,.2f}. "
                "You may correct values, add missed lines, or remove false detections before importing."
            )
            confirm_line_purchases = st.checkbox(
                "Confirm adding each detected line as a separate purchase",
                key="confirm_scanned_line_purchases",
            )
            if st.button(
                "Add detected lines as separate purchases",
                disabled=not confirm_line_purchases or edited_purchase_lines.empty,
                type="primary", key="add_scanned_line_purchases",
            ):
                template = scanned_purchase_rows[0]
                corrected_rows = []
                for index, edited_line in edited_purchase_lines.iterrows():
                    description = str(edited_line.get("description") or "").strip()
                    if not description:
                        continue
                    quantity = max(1, int(edited_line.get("quantity") or 1))
                    line_price = round(float(edited_line.get("purchase_price") or 0), 2)
                    line_shipping = round(float(edited_line.get("shipping") or 0), 2)
                    line_tax = round(float(edited_line.get("tax") or 0), 2)
                    purchase_date_value = st.session_state.get(
                        "purchase_add_date", template["purchase_date"]
                    )
                    if hasattr(purchase_date_value, "isoformat"):
                        purchase_date_value = purchase_date_value.isoformat()
                    identity = "|".join((
                        str(st.session_state.get("purchase_add_receipt_ref", template["receipt_ref"])),
                        str(purchase_date_value), str(index), description,
                        str(quantity), str(line_price),
                    ))
                    corrected_rows.append({
                        **template,
                        "source_key": "receipt:" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                        "purchase_date": str(purchase_date_value),
                        "description": description,
                        "category": str(st.session_state.get("purchase_add_category", template["category"])),
                        "set_name": str(st.session_state.get("purchase_add_set_name", "")),
                        "quantity": quantity,
                        "vendor": str(st.session_state.get("purchase_add_vendor", template["vendor"])),
                        "purchase_price": line_price,
                        "shipping": line_shipping,
                        "tax": line_tax,
                        "total_cost": round(line_price + line_shipping + line_tax, 2),
                        "payment_account": str(st.session_state.get(
                            "purchase_add_payment_account", template["payment_account"]
                        )),
                        "receipt_ref": str(st.session_state.get(
                            "purchase_add_receipt_ref", template["receipt_ref"]
                        )),
                        "notes": str(st.session_state.get("purchase_add_notes", template["notes"])),
                    })
                inserted = database.import_purchases(corrected_rows)
                st.success(
                    f"Added {inserted:,} separate purchase(s). Existing lines from this receipt were skipped."
                )
                st.session_state.pop("purchase_scan_message", None)
                st.session_state.pop("purchase_scan_items", None)
                st.session_state.pop("purchase_scan_rows", None)
                st.session_state.pop("purchase_scan_editor_token", None)
                st.rerun()
    with st.expander("Add purchase", expanded=bool(st.session_state.get("purchase_scan_message"))):
        with st.form("add_purchase_form", clear_on_submit=True):
            first = st.columns(4)
            purchase_date = first[0].date_input("Purchase date", key="purchase_add_date")
            description = first[1].text_input("Description *", key="purchase_add_description")
            category = first[2].text_input(
                "Category", placeholder="Cards, sealed product…", key="purchase_add_category"
            )
            set_name = first[3].text_input("Set", key="purchase_add_set_name")
            second = st.columns(4)
            quantity = second[0].number_input(
                "Quantity", min_value=1, value=1, step=1, key="purchase_add_quantity"
            )
            cards_expected = second[1].number_input(
                "Card count", min_value=0, value=0, step=1,
                help="Total number of individual cards included in this purchase.",
                key="purchase_add_cards_expected",
            )
            vendor = second[2].text_input("Vendor", key="purchase_add_vendor")
            payment_account = second[3].text_input(
                "Payment account", key="purchase_add_payment_account"
            )
            third = st.columns(4)
            purchase_price = third[0].number_input(
                "Purchase price", min_value=0.0, format="%.2f", key="purchase_add_purchase_price"
            )
            shipping = third[1].number_input(
                "Shipping", min_value=0.0, format="%.2f", key="purchase_add_shipping"
            )
            tax = third[2].number_input(
                "Tax", min_value=0.0, format="%.2f", key="purchase_add_tax"
            )
            receipt_ref = third[3].text_input(
                "Receipt reference", key="purchase_add_receipt_ref"
            )
            notes = st.text_area("Notes", key="purchase_add_notes")
            submitted = st.form_submit_button("Add purchase", type="primary")
        if submitted:
            if not description.strip():
                st.error("Description is required.")
            else:
                total_cost = round(float(purchase_price) + float(shipping) + float(tax), 2)
                purchase_id = database.add_purchase({
                    "purchase_date": purchase_date.isoformat(),
                    "description": description.strip(), "category": category.strip(),
                    "set_name": set_name.strip(), "quantity": int(quantity),
                    "cards_expected": int(cards_expected), "vendor": vendor.strip(),
                    "purchase_price": float(purchase_price), "shipping": float(shipping),
                    "tax": float(tax), "total_cost": total_cost,
                    "payment_account": payment_account.strip(), "receipt_ref": receipt_ref.strip(),
                    "notes": notes.strip(), "status": "Unallocated",
                })
                st.success(f"Added purchase P-{purchase_id:05d} with landed cost ${total_cost:,.2f}.")
                st.session_state.pop("purchase_scan_message", None)
                st.session_state.pop("purchase_scan_items", None)
                st.session_state.pop("purchase_scan_rows", None)
                st.session_state.pop("purchase_scan_editor_token", None)

    purchases = database.all_purchases()
    if not purchases:
        st.info("No purchases have been added yet.")
        return
    frame = pd.DataFrame(purchases)
    with st.expander("Filter purchases", expanded=True):
        filter_columns = st.columns(5)
        purchase_search = filter_columns[0].text_input(
            "Search", key="purchase_search", placeholder="Description, set, vendor…"
        )
        category_filter = filter_columns[1].multiselect(
            "Category", sorted(value for value in frame["category"].dropna().unique() if value),
            key="purchase_category_filter",
        )
        vendor_filter = filter_columns[2].multiselect(
            "Vendor", sorted(value for value in frame["vendor"].dropna().unique() if value),
            key="purchase_vendor_filter",
        )
        status_filter = filter_columns[3].multiselect(
            "Status", sorted(value for value in frame["status"].dropna().unique() if value),
            key="purchase_status_filter",
        )
        parsed_dates = pd.to_datetime(frame["purchase_date"], errors="coerce").dropna()
        date_filter = filter_columns[4].date_input(
            "Purchase date range",
            value=(parsed_dates.min().date(), parsed_dates.max().date()),
            key="purchase_date_filter",
        ) if not parsed_dates.empty else ()

    filtered_frame = frame.copy()
    if purchase_search.strip():
        search_columns = ["description", "set_name", "vendor", "receipt_ref", "notes"]
        matches = pd.Series(False, index=filtered_frame.index)
        for column in search_columns:
            matches |= filtered_frame[column].fillna("").map(
                lambda value: matches_terms(value, purchase_search)
            )
        filtered_frame = filtered_frame[matches]
    if category_filter:
        filtered_frame = filtered_frame[filtered_frame["category"].isin(category_filter)]
    if vendor_filter:
        filtered_frame = filtered_frame[filtered_frame["vendor"].isin(vendor_filter)]
    if status_filter:
        filtered_frame = filtered_frame[filtered_frame["status"].isin(status_filter)]
    if len(date_filter) == 2:
        purchase_dates = pd.to_datetime(filtered_frame["purchase_date"], errors="coerce").dt.date
        filtered_frame = filtered_frame[
            purchase_dates.between(date_filter[0], date_filter[1], inclusive="both")
        ]

    with st.expander("Edit purchase"):
        if filtered_frame.empty:
            st.info("No filtered purchases are available to edit.")
        else:
            purchase_choices = {
                f"P-{int(row['id']):05d} — {row['description']}": row
                for row in filtered_frame.to_dict("records")
            }
            selected_purchase_label = st.selectbox(
                "Purchase to edit", purchase_choices, key="edit_purchase_selection"
            )
            selected_purchase = purchase_choices[selected_purchase_label]
            selected_purchase_id = int(selected_purchase["id"])
            parsed_purchase_date = pd.to_datetime(
                selected_purchase.get("purchase_date"), errors="coerce"
            )
            edit_date_default = (
                parsed_purchase_date.date() if not pd.isna(parsed_purchase_date)
                else pd.Timestamp.today().date()
            )
            with st.form(f"edit_purchase_form_{selected_purchase_id}"):
                edit_first = st.columns(4)
                edit_date = edit_first[0].date_input("Purchase date", value=edit_date_default)
                edit_description = edit_first[1].text_input(
                    "Description *", value=str(selected_purchase.get("description") or "")
                )
                edit_category = edit_first[2].text_input(
                    "Category", value=str(selected_purchase.get("category") or "")
                )
                edit_set = edit_first[3].text_input(
                    "Set", value=str(selected_purchase.get("set_name") or "")
                )
                edit_second = st.columns(4)
                edit_quantity = edit_second[0].number_input(
                    "Quantity", min_value=1, value=max(1, int(selected_purchase.get("quantity") or 1)),
                    step=1,
                )
                edit_card_count = edit_second[1].number_input(
                    "Card count", min_value=0,
                    value=max(0, int(selected_purchase.get("cards_expected") or 0)), step=1,
                )
                edit_vendor = edit_second[2].text_input(
                    "Vendor", value=str(selected_purchase.get("vendor") or "")
                )
                edit_account = edit_second[3].text_input(
                    "Payment account", value=str(selected_purchase.get("payment_account") or "")
                )
                edit_third = st.columns(4)
                edit_price = edit_third[0].number_input(
                    "Purchase price", min_value=0.0,
                    value=float(selected_purchase.get("purchase_price") or 0), format="%.2f",
                )
                edit_shipping = edit_third[1].number_input(
                    "Shipping", min_value=0.0,
                    value=float(selected_purchase.get("shipping") or 0), format="%.2f",
                )
                edit_tax = edit_third[2].number_input(
                    "Tax", min_value=0.0, value=float(selected_purchase.get("tax") or 0),
                    format="%.2f",
                )
                edit_receipt = edit_third[3].text_input(
                    "Receipt reference", value=str(selected_purchase.get("receipt_ref") or "")
                )
                edit_notes = st.text_area(
                    "Notes", value=str(selected_purchase.get("notes") or "")
                )
                edit_submitted = st.form_submit_button("Save purchase changes", type="primary")
            if edit_submitted:
                if not edit_description.strip():
                    st.error("Description is required.")
                else:
                    edited_total = round(float(edit_price) + float(edit_shipping) + float(edit_tax), 2)
                    database.update_purchase(selected_purchase_id, {
                        "purchase_date": edit_date.isoformat(),
                        "description": edit_description.strip(), "category": edit_category.strip(),
                        "set_name": edit_set.strip(), "quantity": int(edit_quantity),
                        "cards_expected": int(edit_card_count), "vendor": edit_vendor.strip(),
                        "purchase_price": float(edit_price), "shipping": float(edit_shipping),
                        "tax": float(edit_tax), "total_cost": edited_total,
                        "payment_account": edit_account.strip(), "receipt_ref": edit_receipt.strip(),
                        "notes": edit_notes.strip(),
                    })
                    st.success(f"Updated purchase P-{selected_purchase_id:05d}.")
                    st.rerun()
    total = filtered_frame["total_cost"].sum()
    card_count = int(filtered_frame["cards_expected"].fillna(0).sum())
    allocated = int((filtered_frame["status"] == "Allocated").sum())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Purchases", len(filtered_frame), f"{len(filtered_frame):,} of {len(frame):,}")
    c2.metric("Card count", f"{card_count:,}")
    c3.metric("Landed cost", f"${total:,.2f}")
    c4.metric("Allocated", f"{allocated:,} of {len(filtered_frame):,}")
    st.dataframe(
        filtered_frame[
            [
                "purchase_date", "description", "category", "set_name", "quantity",
                "cards_expected", "vendor", "purchase_price", "shipping", "tax",
                "total_cost", "payment_account", "receipt_ref", "status",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            "cards_expected": st.column_config.NumberColumn("Card count", format="%d"),
            **{
                column: st.column_config.NumberColumn(
                    column.replace("_", " ").title(), format="$%.2f"
                )
                for column in ["purchase_price", "shipping", "tax", "total_cost"]
            },
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
        and int(card.get("quantity") or 0) > 0
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
        try:
            allocations = equal_card_allocations(purchase["total_cost"], selected_cards)
            database.allocate_purchase(purchase["id"], allocations)
            st.success(
                f"Allocated ${sum(row['allocated_total'] for row in allocations):,.2f} "
                f"across {selected_units:,} physical cards."
            )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

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
    with st.expander("Scan receipt", expanded=True):
        receipt_upload = st.file_uploader(
            "Receipt PDF or photo",
            type=["pdf", "jpg", "jpeg", "png", "webp", "heic", "heif"],
            key="expense_receipt_scanner",
        )
        st.caption(
            "Supports text PDFs and receipt photos, including iPhone HEIC. "
            "Review every detected value before adding the expense."
        )
        if st.button("Scan and prefill expense", disabled=receipt_upload is None):
            try:
                if receipt_upload.name.casefold().endswith(".pdf"):
                    scanned = scan_receipt_pdf(receipt_upload.getvalue(), receipt_upload.name)
                else:
                    scanned = scan_receipt_image(receipt_upload.getvalue(), receipt_upload.name)
                if scanned.get("expense_date"):
                    scanned_date = pd.to_datetime(scanned["expense_date"], errors="coerce")
                    if not pd.isna(scanned_date):
                        st.session_state.expense_add_date = scanned_date.date()
                for field in (
                    "vendor", "description", "category", "quantity", "amount", "tax",
                    "shipping", "payment_account", "receipt_ref", "notes",
                ):
                    st.session_state[f"expense_add_{field}"] = scanned[field]
                st.session_state.expense_scan_items = scanned.get("line_items", [])
                st.session_state.expense_scan_message = (
                    f"Detected ${scanned['total']:,.2f} from {receipt_upload.name}. "
                    "Review the prefilled form below."
                )
                st.rerun()
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
        if st.session_state.get("expense_scan_message"):
            st.success(st.session_state.expense_scan_message)
        if st.session_state.get("expense_scan_items"):
            st.markdown("#### Detected line items")
            st.dataframe(
                pd.DataFrame(st.session_state.expense_scan_items),
                use_container_width=True, hide_index=True,
                column_config={
                    "unit_price": st.column_config.NumberColumn("Unit price", format="$%.2f"),
                    "total": st.column_config.NumberColumn("Line total", format="$%.2f"),
                },
            )
    with st.expander("Add expense", expanded=bool(st.session_state.get("expense_scan_message"))):
        with st.form("add_expense_form", clear_on_submit=True):
            first = st.columns(4)
            expense_date = first[0].date_input("Expense date", key="expense_add_date")
            vendor = first[1].text_input("Vendor", key="expense_add_vendor")
            expense_description = first[2].text_input(
                "Description *", key="expense_add_description"
            )
            expense_category = first[3].text_input(
                "Category", placeholder="Supplies, fees…", key="expense_add_category"
            )
            second = st.columns(4)
            expense_quantity = second[0].number_input(
                "Quantity", min_value=1, value=1, step=1, key="expense_add_quantity"
            )
            amount = second[1].number_input(
                "Amount per item", min_value=0.0, format="%.2f", key="expense_add_amount"
            )
            expense_tax = second[2].number_input(
                "Tax", min_value=0.0, format="%.2f", key="expense_add_tax"
            )
            expense_shipping = second[3].number_input(
                "Shipping", min_value=0.0, format="%.2f", key="expense_add_shipping"
            )
            third = st.columns(2)
            expense_account = third[0].text_input(
                "Payment account", key="expense_add_payment_account"
            )
            expense_receipt = third[1].text_input(
                "Receipt reference", key="expense_add_receipt_ref"
            )
            expense_notes = st.text_area("Notes", key="expense_add_notes")
            expense_submitted = st.form_submit_button("Add expense", type="primary")
        if expense_submitted:
            if not expense_description.strip():
                st.error("Description is required.")
            else:
                expense_total = round(
                    float(amount) * int(expense_quantity) + float(expense_tax) + float(expense_shipping), 2
                )
                expense_id = database.add_expense({
                    "expense_date": expense_date.isoformat(), "vendor": vendor.strip(),
                    "description": expense_description.strip(), "quantity": int(expense_quantity),
                    "category": expense_category.strip(), "amount": float(amount),
                    "tax": float(expense_tax), "shipping": float(expense_shipping),
                    "total": expense_total, "payment_account": expense_account.strip(),
                    "receipt_ref": expense_receipt.strip(), "notes": expense_notes.strip(),
                })
                st.success(f"Added expense E-{expense_id:05d} totaling ${expense_total:,.2f}.")
                st.session_state.pop("expense_scan_message", None)
                st.session_state.pop("expense_scan_items", None)
    expenses = database.all_expenses()
    if not expenses:
        st.info("No expenses have been added yet.")
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
    with st.expander("Import eBay sales report"):
        _preview_import(
            "ebay_workbook",
            "eBay transaction workbook, Listings Sales Report CSV, or All Orders CSV",
            read_ebay_workbook,
            database.import_sales,
            "Import eBay sales",
            ["xlsx", "csv"],
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
        "item_subtotal", "cash_received", "trade_value", "shipping_charged", "fees", "promoted_listing_fees",
        "shipping_label_cost", "refunded_amount", "additional_expenses",
        "return_shipping_cost", "net_amount", "cost_of_goods", "profit",
    ]
    for column in monetary_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    frame["sale_datetime"] = pd.to_datetime(frame["sale_date"], errors="coerce")

    with st.expander("Edit sale"):
        sale_choices = {
            f"S-{int(sale['id']):05d} — {sale['sale_date']} — {sale['title']}": sale
            for sale in sales
        }
        edit_sale_label = st.selectbox("Sale to edit", sale_choices, key="edit_sale_selection")
        edit_sale = sale_choices[edit_sale_label]
        edit_sale_id = int(edit_sale["id"])
        parsed_sale_date = pd.to_datetime(edit_sale.get("sale_date"), errors="coerce")
        edit_sale_date_default = (
            parsed_sale_date.date() if not pd.isna(parsed_sale_date) else pd.Timestamp.today().date()
        )
        payment_options = ["", "Cash", "Trade-in value", "Cash + trade", "eBay managed payments", "Other"]
        current_payment = str(edit_sale.get("payment_method") or "")
        if current_payment not in payment_options:
            payment_options.append(current_payment)
        with st.form(f"edit_sale_form_{edit_sale_id}"):
            edit_row_1 = st.columns(4)
            edit_sale_date = edit_row_1[0].date_input("Sale date", value=edit_sale_date_default)
            edit_marketplace = edit_row_1[1].text_input(
                "Marketplace", value=str(edit_sale.get("marketplace") or "")
            )
            edit_order = edit_row_1[2].text_input(
                "Order number", value=str(edit_sale.get("order_number") or "")
            )
            edit_item_id = edit_row_1[3].text_input(
                "Marketplace item ID", value=str(edit_sale.get("item_id") or "")
            )
            edit_row_2 = st.columns(4)
            edit_title = edit_row_2[0].text_input(
                "Title *", value=str(edit_sale.get("title") or "")
            )
            edit_buyer = edit_row_2[1].text_input(
                "Buyer", value=str(edit_sale.get("buyer") or "")
            )
            edit_sku = edit_row_2[2].text_input("SKU", value=str(edit_sale.get("sku") or ""))
            edit_payment = edit_row_2[3].selectbox(
                "Payment method", payment_options, index=payment_options.index(current_payment)
            )
            manual_consideration = edit_payment in {"Cash", "Trade-in value", "Cash + trade"}
            edit_row_3 = st.columns(4)
            edit_subtotal_input = edit_row_3[0].number_input(
                "Item subtotal", min_value=0.0, value=float(edit_sale.get("item_subtotal") or 0),
                format="%.2f", disabled=manual_consideration,
            )
            edit_cash = edit_row_3[1].number_input(
                "Cash received", min_value=0.0, value=float(edit_sale.get("cash_received") or 0),
                format="%.2f", disabled=edit_payment == "Trade-in value",
            )
            edit_trade = edit_row_3[2].number_input(
                "Trade-in value", min_value=0.0, value=float(edit_sale.get("trade_value") or 0),
                format="%.2f", disabled=edit_payment in {"", "Cash", "eBay managed payments", "Other"},
            )
            edit_tax = edit_row_3[3].number_input(
                "Tax collected", min_value=0.0, value=float(edit_sale.get("tax_collected") or 0),
                format="%.2f",
            )
            edit_row_4 = st.columns(4)
            edit_shipping = edit_row_4[0].number_input(
                "Shipping charged", min_value=0.0, value=float(edit_sale.get("shipping_charged") or 0),
                format="%.2f",
            )
            edit_fees = edit_row_4[1].number_input(
                "Platform fees", min_value=0.0, value=float(edit_sale.get("fees") or 0), format="%.2f"
            )
            edit_promoted = edit_row_4[2].number_input(
                "Promoted fees", min_value=0.0,
                value=float(edit_sale.get("promoted_listing_fees") or 0), format="%.2f",
            )
            edit_label = edit_row_4[3].number_input(
                "Shipping label cost", min_value=0.0,
                value=float(edit_sale.get("shipping_label_cost") or 0), format="%.2f",
            )
            edit_notes = st.text_area("Notes", value=str(edit_sale.get("notes") or ""))
            st.caption("Editing a sale does not change inventory quantities or its linked inventory card.")
            edit_sale_submitted = st.form_submit_button("Save sale changes", type="primary")
        if edit_sale_submitted:
            if not edit_title.strip():
                st.error("Title is required.")
            else:
                effective_cash = 0.0 if edit_payment == "Trade-in value" else float(edit_cash)
                effective_trade = float(edit_trade) if edit_payment in {"Trade-in value", "Cash + trade"} else 0.0
                edited_subtotal = (
                    effective_cash + effective_trade if manual_consideration else float(edit_subtotal_input)
                )
                database.update_sale(edit_sale_id, {
                    "sale_date": edit_sale_date.isoformat(), "marketplace": edit_marketplace.strip(),
                    "order_number": edit_order.strip(), "item_id": edit_item_id.strip(),
                    "sku": edit_sku.strip(), "title": edit_title.strip(), "buyer": edit_buyer.strip(),
                    "payment_method": edit_payment, "cash_received": round(effective_cash, 2),
                    "trade_value": round(effective_trade, 2), "item_subtotal": round(edited_subtotal, 2),
                    "shipping_charged": round(edit_shipping, 2), "tax_collected": round(edit_tax, 2),
                    "fees": round(edit_fees, 2), "promoted_listing_fees": round(edit_promoted, 2),
                    "shipping_label_cost": round(edit_label, 2), "notes": edit_notes.strip(),
                })
                st.success(f"Updated sale S-{edit_sale_id:05d}.")
                st.rerun()

        st.divider()
        st.markdown("#### Delete sale")
        st.caption(
            "Delete sale only removes the sales record without changing inventory. "
            "Delete and restore also returns linked sale-item quantities to inventory."
        )
        confirm_delete_sale = st.checkbox(
            f"I understand S-{edit_sale_id:05d} — {edit_sale['title']} will be permanently deleted",
            key=f"delete_sale_confirm_{edit_sale_id}",
        )
        delete_columns = st.columns(2)
        delete_only = delete_columns[0].button(
            "Delete sale only", disabled=not confirm_delete_sale,
            key=f"delete_sale_only_{edit_sale_id}",
            help="Permanently deletes the sale and leaves inventory unchanged.",
        )
        delete_and_restore = delete_columns[1].button(
            "Delete sale + restore inventory",
            disabled=not confirm_delete_sale or bool(edit_sale.get("inventory_restored")),
            key=f"delete_sale_restore_{edit_sale_id}",
            help=(
                "Use only if this sale originally reduced inventory. Imported and automatically "
                "matched sales normally did not reduce inventory."
            ),
        )
        if delete_only:
            database.delete_sale(edit_sale_id, restore_inventory=False)
            st.success("Sale permanently deleted. Inventory was not changed.")
            st.rerun()
        if delete_and_restore:
            database.delete_sale(edit_sale_id, restore_inventory=True)
            st.success("Sale permanently deleted and linked item quantities restored to inventory.")
            st.rerun()

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
                "payment_method", "cash_received", "trade_value", "item_subtotal",
                "shipping_charged", "fees", "shipping_label_cost", "net_amount",
                "refunded_amount", "additional_expenses", "return_shipping_cost",
                "cost_of_goods", "profit", "status", "inventory_restored", "card_id",
            ]
        ],
        use_container_width=True,
        hide_index=True,
        column_config={
            column: st.column_config.NumberColumn(column.replace("_", " ").title(), format="$%.2f")
            for column in [
                "cash_received", "trade_value", "item_subtotal", "shipping_charged", "fees", "shipping_label_cost",
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
    tier_frame = pd.DataFrame(PSA_SERVICE_TIERS)
    tier_frame = tier_frame.rename(columns={
        "name": "Tier",
        "fee": "Fee",
        "max_declared_value": "Max declared value",
        "turnaround": "Turnaround",
    })
    with st.expander("PSA service tiers"):
        st.dataframe(
            tier_frame,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Fee": st.column_config.NumberColumn("Fee", format="$%.2f"),
                "Max declared value": st.column_config.NumberColumn(
                    "Max declared value", format="$%.0f"
                ),
            },
        )
    settings = st.columns(4)
    use_psa_tiers = settings[0].checkbox(
        "Use PSA tier fees",
        value=True,
        key="opportunity_use_psa_tiers",
    )
    declared_value_basis = settings[1].selectbox(
        "Declared value basis",
        ["PSA 10 value", "Expected value", "Grade 9 value", "Highest available graded value"],
        key="opportunity_declared_value_basis",
    )
    manual_grading_cost = settings[2].number_input(
        "Manual cost per card", min_value=0.0, value=30.0, format="%.2f",
        disabled=use_psa_tiers,
        key="opportunity_grading_cost",
    )
    selling_cost_percent = settings[3].number_input(
        "Selling costs %", min_value=0.0, max_value=100.0, value=15.0, format="%.1f",
        key="opportunity_selling_cost",
    )
    probability_settings = st.columns(3)
    probability_8 = probability_settings[0].number_input(
        "Chance of 8/8.5 %", min_value=0.0, max_value=100.0, value=20.0, format="%.1f",
        key="opportunity_probability_8",
    )
    probability_9 = probability_settings[1].number_input(
        "Chance of 9 %", min_value=0.0, max_value=100.0, value=50.0, format="%.1f",
        key="opportunity_probability_9",
    )
    probability_10 = probability_settings[2].number_input(
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
        probabilities = {
            "8 / 8.5": probability_8 / 100,
            "9": probability_9 / 100,
            "10": probability_10 / 100,
        }
        opportunity_rows = []
        for card in candidates:
            grade_values = {
                "8 / 8.5": float(card.get("graded_8_price") or 0),
                "9": float(card.get("graded_9_price") or 0),
                "10": float(card.get("psa_10_price") or 0),
            }
            expected_declared_value = sum(
                grade_values[grade] * probabilities[grade] for grade in grade_values
            )
            declared_value = (
                expected_declared_value
                if declared_value_basis == "Expected value"
                else psa_declared_value(card, declared_value_basis)
            )
            recommended_tier = psa_tier_for_value(declared_value) if use_psa_tiers else None
            grading_cost = (
                float(recommended_tier["fee"])
                if recommended_tier
                else float(manual_grading_cost)
            )
            row = grading_opportunity(
                card,
                grading_cost,
                selling_cost_percent / 100,
                probabilities,
                grading_tier=recommended_tier,
                declared_value=declared_value,
            )
            if use_psa_tiers and recommended_tier is None:
                row["recommended_tier"] = "Above Walk-Through"
                row["tier_covered"] = False
                row["grading_decision"] = "Review"
            opportunity_rows.append(row)
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
            metrics[2].metric(
                "Best tier / expected profit",
                top["recommended_tier"],
                f"${top['expected_profit']:,.2f}",
            )
            money_columns = [
                "cost_basis", "raw_value", "grade_8_value", "grade_9_value", "psa_10_value",
                "declared_value", "tier_fee", "tier_max_value", "grading_cost",
                "grade_8_profit", "grade_9_profit", "psa_10_profit",
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

    render_tax_summary(purchases, expenses, sales)

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
