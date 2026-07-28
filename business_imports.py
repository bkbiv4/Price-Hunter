"""Workbook import and equal-cost allocation rules for Price Hunter."""

from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip()
    return "" if text in {"--", "nan", "NaT"} else text


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, str) and value.strip().startswith("#"):
        return None
    try:
        number = float(value)
        return None if math.isnan(number) else number
    except (TypeError, ValueError):
        return None


def _integer(value: Any, default: int = 0) -> int:
    number = _number(value)
    return default if number is None else max(0, int(number))


def _date(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    number = _number(value)
    if number is not None and 1 <= number <= 100000:
        return (datetime(1899, 12, 30) + timedelta(days=number)).date().isoformat()
    return _text(value)


def _source_key(prefix: str, *parts: Any) -> str:
    payload = "|".join(_text(part).casefold() for part in parts)
    return f"{prefix}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def read_purchase_workbook(source: Any) -> tuple[list[dict[str, Any]], list[str]]:
    frame = pd.read_excel(
        source,
        sheet_name="Inventory Purchase Log",
        header=3,
        usecols="C:T",
    )
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    occurrences: dict[tuple[Any, ...], int] = {}
    for index, raw in frame.iterrows():
        description = _text(raw.get("Description"))
        if not description or description.casefold() == "total":
            continue
        purchase_price = _number(raw.get("Purchase Price"))
        shipping = _number(raw.get("Shipping Paid")) or 0.0
        tax = _number(raw.get("Taxes")) or 0.0
        total = _number(raw.get("Total Cost"))
        if purchase_price is None:
            errors.append(f"Purchase row {index + 5}: {description} has no valid purchase price.")
            continue
        calculated_total = round(purchase_price + shipping + tax, 2)
        total = calculated_total if total is None else round(total, 2)
        purchase_date = _date(raw.get("Date Purchased"))
        vendor = _text(raw.get("From"))
        set_name = _text(raw.get("Sub-Category"))
        receipt_ref = _text(raw.get("Receipt Saved?"))
        identity = (purchase_date, description, vendor, total, receipt_ref, set_name)
        occurrences[identity] = occurrences.get(identity, 0) + 1
        rows.append({
            "source_key": _source_key(
                "purchase", *identity, occurrences[identity]
            ),
            "purchase_date": purchase_date,
            "description": description,
            "category": _text(raw.get("Category")),
            "set_name": set_name,
            "quantity": max(1, _integer(raw.get("Qty"), 1)),
            "cards_expected": _integer(raw.get("Cards")),
            "vendor": vendor,
            "purchase_price": round(purchase_price, 2),
            "shipping": round(shipping, 2),
            "tax": round(tax, 2),
            "total_cost": total,
            "payment_account": _text(raw.get("Paid From")),
            "receipt_ref": receipt_ref,
            "notes": _text(raw.get("Notes")),
        })
    return rows, errors


def read_expense_workbook(source: Any) -> tuple[list[dict[str, Any]], list[str]]:
    frame = pd.read_excel(
        source,
        sheet_name="Hidden Richz LLC Expenses",
        header=2,
        usecols="C:M",
    )
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    occurrences: dict[tuple[Any, ...], int] = {}
    for index, raw in frame.iterrows():
        description = _text(raw.get("Description"))
        if not description or description.casefold() == "total":
            continue
        amount = _number(raw.get("Amount"))
        quantity = max(1, _integer(raw.get("Qty"), 1))
        tax = _number(raw.get("Taxes")) or 0.0
        if amount is None:
            errors.append(f"Expense row {index + 4}: {description} has no valid amount.")
            continue
        total = _number(raw.get("Total"))
        total = round(amount * quantity + tax, 2) if total is None else round(total, 2)
        expense_date = _date(raw.get("Date"))
        vendor = _text(raw.get("Vendor"))
        receipt_ref = _text(raw.get("Receipt Saved?"))
        identity = (expense_date, vendor, description, total, receipt_ref)
        occurrences[identity] = occurrences.get(identity, 0) + 1
        rows.append({
            "source_key": _source_key(
                "expense", *identity, occurrences[identity]
            ),
            "expense_date": expense_date,
            "vendor": vendor,
            "description": description,
            "quantity": quantity,
            "category": _text(raw.get("Category")),
            "amount": round(amount, 2),
            "tax": round(tax, 2),
            "shipping": 0.0,
            "total": total,
            "payment_account": _text(raw.get("Paid From")),
            "receipt_ref": receipt_ref,
            "notes": _text(raw.get("Notes")),
        })
    return rows, errors


def read_ebay_workbook(source: Any) -> tuple[list[dict[str, Any]], list[str]]:
    excel = pd.ExcelFile(source)
    report_sheet = next(
        (name for name in excel.sheet_names if name.startswith("Transaction_report_")),
        None,
    )
    if not report_sheet:
        return [], ["No eBay Transaction_report sheet was found."]
    frame = pd.read_excel(excel, sheet_name=report_sheet, header=11)
    shipping_rows = frame[frame["Type"].astype(str).eq("Shipping label")]
    shipping_by_order = (
        shipping_rows.assign(_net=pd.to_numeric(shipping_rows["Net amount"], errors="coerce").fillna(0))
        .groupby("Order number")["_net"]
        .sum()
        .to_dict()
    )
    rows: list[dict[str, Any]] = []
    for _, raw in frame[frame["Type"].astype(str).eq("Order")].iterrows():
        order_number = _text(raw.get("Order number"))
        gross = _number(raw.get("Gross transaction amount")) or 0.0
        order_net = _number(raw.get("Net amount")) or 0.0
        label_cost = max(0.0, -float(shipping_by_order.get(raw.get("Order number"), 0.0)))
        sale_date = _date(raw.get("Transaction creation date"))
        item_id = _text(raw.get("Item ID"))
        rows.append({
            "source_key": _source_key("ebay", order_number, item_id, sale_date),
            "sale_date": sale_date,
            "marketplace": "eBay",
            "order_number": order_number,
            "item_id": item_id,
            "sku": _text(raw.get("Custom label")),
            "title": _text(raw.get("Item title")),
            "quantity": max(1, _integer(raw.get("Quantity"), 1)),
            "item_subtotal": round(_number(raw.get("Item subtotal")) or 0.0, 2),
            "shipping_charged": round(_number(raw.get("Shipping and handling")) or 0.0, 2),
            "fees": round(max(0.0, gross - order_net), 2),
            "shipping_label_cost": round(label_cost, 2),
            "net_amount": round(order_net - label_cost, 2),
        })
    return rows, []


def equal_card_allocations(
    total_cost: float,
    cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Allocate purchase cents equally across physical cards and reconcile exactly."""
    total_units = sum(max(0, int(card.get("quantity", 0))) for card in cards)
    if total_units <= 0:
        raise ValueError("At least one physical card is required for allocation.")
    total_cents = int(round(float(total_cost) * 100))
    base_cents, remainder = divmod(total_cents, total_units)
    allocations: list[dict[str, Any]] = []
    for card in cards:
        quantity = max(0, int(card.get("quantity", 0)))
        higher_units = min(quantity, remainder)
        remainder -= higher_units
        allocated_cents = base_cents * quantity + higher_units
        allocations.append({
            "card_id": int(card["id"]),
            "quantity": quantity,
            "base_unit_cost": base_cents / 100,
            "higher_cost_units": higher_units,
            "allocated_total": allocated_cents / 100,
        })
    return allocations
