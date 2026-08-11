"""Convert SportsCardsPro collection exports into Price Hunter inventory rows."""

from __future__ import annotations

import re
from typing import Any


REQUIRED_COLUMNS = {
    "id",
    "product-name",
    "console-name",
    "price-in-pennies",
    "include-string",
    "cost-basis-in-pennies",
    "quantity",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.casefold() == "nan" else text


def _dollars(value: Any) -> float:
    try:
        return int(float(value or 0)) / 100
    except (TypeError, ValueError):
        return 0.0


def _quantity(value: Any) -> int:
    try:
        return max(1, int(float(value or 1)))
    except (TypeError, ValueError):
        return 1


def collection_type(set_name: Any) -> str:
    """Return the broad inventory type encoded in a SportsCardsPro set name."""
    name = _text(set_name)
    if not name:
        return ""
    if name.casefold().startswith("one piece ") or name.casefold() == "one piece":
        return "One Piece"
    cards_marker = " cards "
    marker_position = name.casefold().find(cards_marker)
    if marker_position > 0:
        return name[:marker_position].strip()
    return name.split(maxsplit=1)[0]


def _grade_details(include: str, grading_company: str) -> tuple[str, str, str]:
    if include.casefold() == "ungraded":
        return "Raw / Ungraded", "", ""
    grade_match = re.search(r"(\d+(?:\.\d+)?)", include)
    grader = grading_company or re.sub(r"\s*\d+(?:\.\d+)?\s*$", "", include).strip()
    return "Graded", grader, grade_match.group(1) if grade_match else ""


def collection_row_to_card(row: dict[str, Any]) -> dict[str, Any]:
    """Map one SportsCardsPro collection CSV row to Price Hunter's schema."""
    include = _text(row.get("include-string")) or "Ungraded"
    condition, grader, grade = _grade_details(include, _text(row.get("grading-company")))
    card_name = _text(row.get("product-name"))
    number_match = re.search(r"#([^\s\]]+)$", card_name)
    notes = " — ".join(
        part for part in (_text(row.get("condition-string")), _text(row.get("notes"))) if part
    )
    market_price = _dollars(row.get("price-in-pennies"))
    card = {
        "scp_id": _text(row.get("id")),
        "card_name": card_name,
        "set_name": _text(row.get("console-name")),
        "card_number": number_match.group(1) if number_match else "",
        "condition": condition,
        "grader": grader,
        "grade": grade,
        "certification_number": _text(row.get("grading-cert-id")),
        "quantity": _quantity(row.get("quantity")),
        "cost": _dollars(row.get("cost-basis-in-pennies")),
        "market_price": market_price,
        "graded_8_price": None,
        "graded_9_price": market_price if include.casefold() == "graded 9" else None,
        "psa_10_price": market_price if include.casefold() == "psa 10" else None,
        "grade_prices_refreshed": 0,
        "grade_prices_refreshed_at": None,
        "storage_location": _text(row.get("folder")),
        "notes": notes,
    }
    return card


def import_identity(card: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Identity used to make repeated imports safe without losing real quantities."""
    return (
        _text(card.get("scp_id")),
        _text(card.get("condition")),
        _text(card.get("grader")),
        _text(card.get("grade")),
        _text(card.get("certification_number")),
    )
