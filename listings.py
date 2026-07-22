"""Rules for consistent inventory SKUs and eBay listing drafts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


EBAY_TITLE_LIMIT = 80


def make_sku(item_id: int) -> str:
    return f"PH-{item_id:06d}"


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def build_title(
    card_name: str,
    set_name: str,
    grade: str = "",
    card_number: str = "",
    grader: str = "",
) -> str:
    parts = [card_name, set_name]
    if card_number and f"#{card_number}" not in card_name:
        parts.append(f"#{card_number}")
    slab_grade = clean_text(f"{grader} {grade}")
    if slab_grade:
        parts.append(slab_grade)
    title = clean_text(" ".join(part for part in parts if part))
    return title[:EBAY_TITLE_LIMIT].rstrip()


def build_description(
    card_name: str,
    set_name: str,
    condition: str,
    grade: str,
    notes: str,
    sku: str,
    grader: str = "",
    certification_number: str = "",
) -> str:
    details = [
        f"Card: {card_name}",
        f"Set: {set_name}",
        f"Condition: {condition or 'See photos'}",
    ]
    if grader:
        details.append(f"Grading company: {grader}")
    if grade:
        details.append(f"Grade: {grade}")
    if certification_number:
        details.append(f"Certification number: {certification_number}")
    if notes:
        details.extend(["", clean_text(notes)])
    details.extend([
        "",
        "Please review all photos for the exact card and condition.",
        "Card will be packaged securely for shipment.",
        f"Inventory ID: {sku}",
    ])
    return "\n".join(details)


def suggested_price(market_price: float | None, cost: float, markup_percent: float) -> float:
    cost_based = Decimal(str(cost)) * (Decimal("1") + Decimal(str(markup_percent)) / 100)
    candidate = max(Decimal(str(market_price or 0)), cost_based)
    return float(candidate.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class ListingDraft:
    title: str
    description: str
    price: float
