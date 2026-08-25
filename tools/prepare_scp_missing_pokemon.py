"""Prepare SportsCardsPro import rows missing from a TCGcollector export.

This is a local reconciliation helper for the two CSV exports in Downloads.
It writes:
- a SportsCardsPro-shaped import CSV
- an audit CSV with source and generated identity fields
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


SCP_COLUMNS = [
    "id",
    "product-name",
    "console-name",
    "price-in-pennies",
    "include-string",
    "condition-string",
    "sku",
    "notes",
    "cost-basis-in-pennies",
    "quantity",
    "date-entered",
    "date-purchased",
    "grading-company",
    "grading-cert-id",
    "folder",
]

BASE_VARIANTS = {"", "normal", "non-holo", "promo"}
PROMO_EXPANSIONS = {"scarlet & violet promos", "sword & shield promos"}
SET_OVERRIDES = {
    "scarlet & violet energies": "Pokemon Scarlet & Violet Energy",
}


def text(value: Any) -> str:
    if value is None:
        return ""
    result = str(value).strip()
    return "" if result.casefold() == "nan" else result


def key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", text(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", normalized.casefold()).strip()


def card_number(value: Any) -> str:
    raw = text(value)
    numerator = raw.split("/", 1)[0].strip()
    if numerator.isdigit():
        return str(int(numerator))
    return numerator.upper()


def pennies(value: Any) -> str:
    raw = text(value).replace("$", "").replace(",", "")
    try:
        return str(round(float(raw) * 100))
    except ValueError:
        return "0"


def quantity(value: Any) -> int:
    try:
        return max(1, int(float(text(value) or 1)))
    except ValueError:
        return 1


def parse_scp_product(product_name: Any) -> tuple[str, str, str]:
    name = text(product_name)
    match = re.search(r"^(.*?)\s+#([A-Za-z0-9-]+)$", name)
    if match:
        name_part = match.group(1)
        number = card_number(match.group(2))
    else:
        name_part = name
        number = ""
    variants = re.findall(r"\[([^]]+)]", name_part)
    base_name = re.sub(r"\s*\[[^]]+]", "", name_part).strip()
    return base_name, number, variants[-1] if variants else ""


def scp_set_name(expansion: str, variant: str) -> str:
    variant_key = variant.casefold()
    expansion_key = expansion.casefold()
    if variant_key.startswith("trick or trade"):
        return f"Pokemon {variant}"
    if expansion_key in PROMO_EXPANSIONS:
        return "Pokemon Promo"
    return SET_OVERRIDES.get(expansion_key, f"Pokemon {expansion}")


def display_variant(expansion: str, variant: str, rarity: str) -> str:
    variant_key = variant.casefold()
    expansion_key = expansion.casefold()
    rarity_key = rarity.casefold()
    if expansion_key in PROMO_EXPANSIONS and variant_key in {"holo", "normal holo", "mirage holo", "promo"}:
        return ""
    if variant_key in BASE_VARIANTS:
        return ""
    if variant_key in {"holo", "normal holo"}:
        if "promo" in rarity_key or "rare" in rarity_key:
            return "Holo"
        return ""
    if variant_key.startswith("trick or trade"):
        return "Holo" if "holo" in rarity_key or rarity_key == "rare" else ""
    if variant_key == "jumbo size":
        return "Jumbo"
    if variant_key == "poké ball reverse holo":
        return "Poke Ball"
    if variant_key == "friend ball reverse holo":
        return "Friend Ball"
    if variant_key == "love ball reverse holo":
        return "Love Ball"
    if variant_key == "dusk ball reverse holo":
        return "Dusk Ball"
    if variant_key == "energy reverse holo":
        return "Reverse Holo"
    if variant_key == "reverse holo" and expansion == "Prismatic Evolutions":
        return "Reverse"
    return variant


def product_name(row: dict[str, str]) -> str:
    name = text(row["Card name"])
    number = card_number(row["Card number"])
    variant = display_variant(text(row["Expansion"]), text(row["Card variant"]), text(row["Rarity"]))
    if variant:
        name = f"{name} [{variant}]"
    if number:
        name = f"{name} #{number}"
    return name


def identity(set_name: str, product: str, condition: str = "Ungraded") -> tuple[str, str, str, str, str]:
    base_name, number, variant = parse_scp_product(product)
    normalized_set = re.sub(r"^pokemon\s+", "", set_name, flags=re.I)
    if key(normalized_set) in {"promo", "scarlet violet promos", "sword shield promos"}:
        normalized_set = "promo"
        if key(variant) == "holo":
            variant = ""
    return (key(normalized_set), key(base_name), key(number), key(variant), key(condition))


def build_existing_quantities(rows: list[dict[str, str]]) -> dict[tuple[str, str, str, str, str], int]:
    totals: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    for row in rows:
        set_name = text(row.get("console-name"))
        if not set_name.casefold().startswith("pokemon"):
            continue
        totals[identity(set_name, text(row.get("product-name")), text(row.get("include-string")))] += quantity(
            row.get("quantity")
        )
    return totals


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tcgcollector", required=True, type=Path)
    parser.add_argument("--sportscardspro", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    args = parser.parse_args()

    tcg_rows = read_csv(args.tcgcollector)
    scp_rows = read_csv(args.sportscardspro)
    existing = build_existing_quantities(scp_rows)

    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    requested: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
    audit_rows: list[dict[str, Any]] = []

    for row in tcg_rows:
        set_name = scp_set_name(text(row["Expansion"]), text(row["Card variant"]))
        product = product_name(row)
        ident = identity(set_name, product)
        requested[ident] += quantity(row["Quantity"])
        grouped.setdefault(
            ident,
            {
                "id": "",
                "product-name": product,
                "console-name": set_name,
                "price-in-pennies": pennies(row["Price"]),
                "include-string": "Ungraded",
                "condition-string": "Normal wear",
                "sku": "",
                "notes": (
                    f"From TCGcollector: {text(row['Expansion'])}; "
                    f"{text(row['Card number'])}; {text(row['Card variant'])}; {text(row['Rarity'])}"
                ),
                "cost-basis-in-pennies": "0",
                "quantity": "0",
                "date-entered": "2026-08-23",
                "date-purchased": "",
                "grading-company": "",
                "grading-cert-id": "",
                "folder": "",
            },
        )
        audit_rows.append(
            {
                "source-expansion": text(row["Expansion"]),
                "source-card-name": text(row["Card name"]),
                "source-card-number": text(row["Card number"]),
                "source-variant": text(row["Card variant"]),
                "source-rarity": text(row["Rarity"]),
                "generated-set": set_name,
                "generated-product-name": product,
                "identity": " | ".join(ident),
                "source-quantity": quantity(row["Quantity"]),
                "existing-quantity": existing.get(ident, 0),
            }
        )

    additions: list[dict[str, Any]] = []
    for ident, wanted in sorted(requested.items(), key=lambda item: (item[0][0], item[0][1], item[0][2], item[0][3])):
        missing = max(0, wanted - existing.get(ident, 0))
        if missing:
            row = grouped[ident].copy()
            row["quantity"] = str(missing)
            additions.append(row)

    audit_fields = [
        "source-expansion",
        "source-card-name",
        "source-card-number",
        "source-variant",
        "source-rarity",
        "generated-set",
        "generated-product-name",
        "identity",
        "source-quantity",
        "existing-quantity",
    ]
    write_csv(args.out, additions, SCP_COLUMNS)
    write_csv(args.audit, audit_rows, audit_fields)

    print(f"TCGcollector rows: {len(tcg_rows)}")
    print(f"SportsCardsPro rows: {len(scp_rows)}")
    print(f"Unique TCG identities: {len(requested)}")
    print(f"Missing identities: {len(additions)}")
    print(f"Missing units: {sum(int(row['quantity']) for row in additions)}")
    print(f"Wrote import: {args.out}")
    print(f"Wrote audit: {args.audit}")


if __name__ == "__main__":
    main()
