"""TCG Collector CSV mapping and duplicate-aware Pokemon reconciliation."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any


REQUIRED_COLUMNS = {
    "Card name", "Card number", "Expansion", "Card variant", "Card language",
    "Card condition", "Quantity", "Price", "Rarity", "Note",
}

BASE_VARIANTS = {"", "normal", "normal holo", "holo", "non-holo", "promo"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.casefold() == "nan" else text


def _ascii_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _text(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _number(value: Any) -> str:
    numerator = _text(value).split("/", 1)[0].strip()
    if numerator.isdigit():
        return str(int(numerator))
    return numerator.upper()


def _money(value: Any) -> float:
    try:
        return float(_text(value).replace("$", "").replace(",", ""))
    except ValueError:
        return 0.0


def _quantity(value: Any) -> int:
    try:
        return max(1, int(float(value)))
    except (TypeError, ValueError):
        return 1


def _set_and_variant(expansion: str, variant: str) -> tuple[str, str]:
    if variant.casefold().startswith("trick or trade"):
        return f"Pokemon {variant}", "Holo"
    if expansion.casefold().endswith(" promos"):
        return "Pokemon Promo", ""
    display_variant = "" if variant.casefold() in BASE_VARIANTS else variant
    return f"Pokemon {expansion}", display_variant


def tcgcollector_row_to_card(row: dict[str, Any]) -> dict[str, Any]:
    """Map one TCG Collector export row to Price Hunter inventory fields."""
    name = _text(row.get("Card name"))
    number = _number(row.get("Card number"))
    source_variant = _text(row.get("Card variant"))
    set_name, display_variant = _set_and_variant(_text(row.get("Expansion")), source_variant)
    card_name = name
    if display_variant:
        card_name += f" [{display_variant}]"
    if number:
        card_name += f" #{number}"
    note_parts = [
        f"TCG Collector: {_text(row.get('Rarity'))}",
        source_variant,
        _text(row.get("Card language")),
        _text(row.get("Card condition")),
        _text(row.get("Note")),
    ]
    return {
        "scp_id": "",
        "card_name": card_name,
        "set_name": set_name,
        "card_number": number,
        "condition": "Raw / Ungraded",
        "grader": "",
        "grade": "",
        "certification_number": "",
        "quantity": _quantity(row.get("Quantity")),
        "cost": 0.0,
        "market_price": _money(row.get("Price")),
        "graded_8_price": None,
        "graded_9_price": None,
        "psa_10_price": None,
        "grade_prices_refreshed": 0,
        "grade_prices_refreshed_at": None,
        "storage_location": "",
        "notes": " | ".join(part for part in note_parts if part),
    }


def pokemon_identity(card: dict[str, Any]) -> tuple[str, str, str, str, str]:
    """Canonical identity shared by SportsCardsPro and TCG Collector Pokemon rows."""
    set_name = re.sub(r"^pokemon\s+", "", _text(card.get("set_name")), flags=re.I)
    if set_name.casefold().endswith(" promos") or set_name.casefold() == "promo":
        set_name = "promo"
    card_name = re.sub(r"\s+#\S+\s*$", "", _text(card.get("card_name")))
    variants = re.findall(r"\[([^]]+)]", card_name)
    variant = variants[-1] if variants else ""
    card_name = re.sub(r"\s*\[[^]]+]", "", card_name).strip()
    if variant.casefold() in BASE_VARIANTS:
        variant = ""
    number = _number(card.get("card_number"))
    return (
        _ascii_key(set_name), _ascii_key(card_name), _ascii_key(number),
        _ascii_key(variant), _ascii_key(card.get("condition")),
    )


def reconcile_collection_rows(
    imported_rows: list[dict[str, Any]], existing_cards: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return only missing quantities, preserving existing inventory without duplication."""
    existing_quantities: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    for card in existing_cards:
        if _text(card.get("set_name")).casefold().startswith("pokemon"):
            existing_quantities[pokemon_identity(card)] += max(0, int(card.get("quantity") or 0))

    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    requested_quantities: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    for row in imported_rows:
        identity = pokemon_identity(row)
        grouped.setdefault(identity, row.copy())
        requested_quantities[identity] += max(0, int(row.get("quantity") or 0))

    additions = []
    matched_rows = 0
    covered_units = 0
    for identity, requested in requested_quantities.items():
        existing = existing_quantities.get(identity, 0)
        missing = max(0, requested - existing)
        covered_units += min(requested, existing)
        if missing:
            addition = grouped[identity].copy()
            addition["quantity"] = missing
            additions.append(addition)
        else:
            matched_rows += 1
    return additions, {
        "export_rows": len(grouped),
        "export_units": sum(requested_quantities.values()),
        "matched_rows": matched_rows,
        "covered_units": covered_units,
        "new_rows": len(additions),
        "new_units": sum(int(row["quantity"]) for row in additions),
    }
