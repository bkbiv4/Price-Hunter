"""Extract draft expense fields from text-based receipt PDFs."""

from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def _money(text: str, *patterns: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def allocate_receipt_amount(total: float, line_totals: list[float]) -> list[float]:
    """Allocate a receipt-level amount proportionally and reconcile exactly to the cent."""
    if not line_totals:
        return []
    total_cents = int(round(float(total) * 100))
    weight_total = sum(max(0.0, float(value)) for value in line_totals)
    if weight_total <= 0:
        base, remainder = divmod(total_cents, len(line_totals))
        return [(base + (1 if index < remainder else 0)) / 100 for index in range(len(line_totals))]
    raw_cents = [total_cents * max(0.0, float(value)) / weight_total for value in line_totals]
    allocated = [int(value) for value in raw_cents]
    remainder = total_cents - sum(allocated)
    order = sorted(
        range(len(raw_cents)), key=lambda index: raw_cents[index] - allocated[index], reverse=True
    )
    for index in order[:remainder]:
        allocated[index] += 1
    return [value / 100 for value in allocated]


def detect_line_items(text: str) -> list[dict[str, Any]]:
    """Extract structured merchandise lines from common receipt OCR text."""
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    amazon_items = []
    for match in re.finditer(
        r"(?:^|\n)(\d+)\s+of:\s*(.+?)\s+\$([\d,]+\.\d{2})(?:\n|$)",
        normalized,
        re.IGNORECASE,
    ):
        quantity = int(match.group(1))
        total = float(match.group(3).replace(",", ""))
        amazon_items.append({
            "description": match.group(2).strip(),
            "quantity": quantity,
            "unit_price": round(total / quantity, 2),
            "total": round(total, 2),
        })
    if amazon_items:
        return amazon_items

    summary_pattern = re.compile(
        r"subtotal|sales tax|grand total|^total\b|shipping|tax\s*\(|authorization|receipt:|contactless",
        re.IGNORECASE,
    )
    metadata_pattern = re.compile(
        r"thank you|all sales|facebook|instagram|@|\b(?:visa|mastercard|amex|discover)\b|"
        r"\d{1,2}:\d{2}|\b\d{5}(?:-\d{4})?\b|\b(?:usd|debit|credit)\b",
        re.IGNORECASE,
    )
    price_pattern = re.compile(r"\$\s*([\d,]+\.\d{2})")
    unit_pattern = re.compile(r"\$\s*([\d,]+\.\d{2})\s*each", re.IGNORECASE)
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending: list[str] = []

    def finish_current() -> None:
        nonlocal current
        if not current:
            return
        description = " ".join(current.pop("parts", [])).strip(" -")
        if description:
            quantity_match = re.search(r"(?:\bx\s*|\bqty\s*)(\d+)\b", description, re.I)
            quantity = int(quantity_match.group(1)) if quantity_match else 1
            total = float(current["total"])
            unit_price = current.get("unit_price")
            if unit_price is None:
                unit_price = round(total / quantity, 2)
            items.append({
                "description": description,
                "quantity": quantity,
                "unit_price": round(float(unit_price), 2),
                "total": round(total, 2),
            })
        current = None

    for line in normalized.splitlines():
        if summary_pattern.search(line):
            finish_current()
            pending = []
            continue
        prices = price_pattern.findall(line)
        unit_match = unit_pattern.search(line)
        cleaned = price_pattern.sub("", line)
        cleaned = re.sub(r"[()]|\beach\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -")
        if unit_match and current:
            current["unit_price"] = float(unit_match.group(1).replace(",", ""))
            if cleaned:
                current["parts"].append(cleaned)
            continue
        if prices:
            finish_current()
            parts = pending + ([cleaned] if cleaned and not metadata_pattern.search(cleaned) else [])
            pending = []
            if parts:
                current = {
                    "parts": parts,
                    "total": float(prices[-1].replace(",", "")),
                }
            continue
        if current and cleaned and not metadata_pattern.search(cleaned):
            current["parts"].append(cleaned)
        elif cleaned and not metadata_pattern.search(cleaned):
            pending.append(cleaned)
            pending = pending[-3:]
    finish_current()
    return items


def parse_receipt_text(text: str, file_name: str = "receipt.pdf") -> dict[str, Any]:
    """Parse common receipt fields, with richer support for Amazon order PDFs."""
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not normalized:
        raise ValueError("No readable text was found. This PDF may require OCR.")
    line_items = detect_line_items(normalized)

    amazon_order = re.search(r"Amazon\.com order number:\s*([\d-]+)", normalized, re.I)
    order_number = amazon_order.group(1) if amazon_order else ""
    date_match = re.search(
        r"(?:Order Placed:\s*)?([A-Za-z]+\s*\d{1,2},\s*\d{4})", normalized, re.I
    )
    expense_date = ""
    if date_match:
        readable_date = re.sub(r"([A-Za-z]+)\s*(\d)", r"\1 \2", date_match.group(1))
        readable_date = re.sub(r",\s*", ", ", readable_date)
        expense_date = datetime.strptime(readable_date, "%B %d, %Y").date().isoformat()

    item_match = re.search(r"(?:^|\n)(\d+)\s+of:\s*([^\n]+)", normalized, re.IGNORECASE)
    quantity = int(item_match.group(1)) if item_match else 1
    item_line = item_match.group(2).strip() if item_match else ""
    line_price = re.search(r"\s+\$([\d,]+\.\d{2})$", item_line)
    description = re.sub(r"\s+\$[\d,]+\.\d{2}$", "", item_line).strip()
    subtotal = _money(
        normalized,
        r"Item\(s\) Subtotal:\s*(?:USD\s*)?\$?([\d,]+\.\d{2})",
        r"(?:^|\n)Subtotal\s*(?:\n|\s)+(?:USD\s*)?\$?([\d,]+\.\d{2})",
    )
    if subtotal is None and line_price:
        subtotal = float(line_price.group(1).replace(",", ""))
    tax = _money(
        normalized,
        r"Sales Tax:\s*(?:USD\s*)?\$?([\d,]+\.\d{2})",
        r"Estimated tax to be collected:\s*(?:USD\s*)?\$?([\d,]+\.\d{2})",
        r"[^\n]*Sales Tax[^\n]*(?:\n|\s)+(?:USD\s*)?\$?([\d,]+\.\d{2})",
    ) or 0.0
    total = _money(
        normalized,
        r"Grand Total:\s*(?:USD\s*)?\$?([\d,]+\.\d{2})",
        r"Order Total:\s*(?:USD\s*)?\$?([\d,]+\.\d{2})",
        r"Total for This Shipment:\s*(?:USD\s*)?\$?([\d,]+\.\d{2})",
        r"(?:^|\n)Total\s*(?:\n|\s)+(?:USD\s*)?\$?([\d,]+\.\d{2})",
    )
    if subtotal is None and total is not None:
        subtotal = max(0.0, total - tax)
    subtotal = subtotal or 0.0
    shipping = round(max(0.0, (total or subtotal + tax) - subtotal - tax), 2)
    unit_amount = round(subtotal / max(1, quantity), 2)

    payment_match = re.search(
        r"Payment Method:\s*\n?([^\n]+?)(?:\s*\|\s*Last digits:\s*(\d+))?(?:\n|$)",
        normalized,
        re.IGNORECASE,
    )
    payment_account = ""
    if payment_match:
        payment_account = payment_match.group(1).strip()
        if payment_match.group(2):
            payment_account += f" ending in {payment_match.group(2)}"
    if not payment_account:
        card_match = re.search(r"\b(Mastercard|Visa|Amex|Discover)\s*(?:ending in\s*)?(\d{4})\b", normalized, re.I)
        if card_match:
            payment_account = f"{card_match.group(1).title()} ending in {card_match.group(2)}"

    category = ""
    if re.search(r"card case|card storage|top loader|sleeve|shipping supplies", description, re.I):
        category = "Supplies"
    if re.search(r"booster box|play booster", normalized, re.I):
        category = "Inventory"
    if not description:
        description = "; ".join(item["description"] for item in line_items) or "Receipt purchase"

    receipt_match = re.search(r"Receipt:\s*([^\s]+)", normalized, re.I)
    vendor = "Amazon.com" if amazon_order else ""
    if not vendor:
        store_match = re.search(r"^([^\n]*\bStore)\s*$", normalized, re.I | re.MULTILINE)
        if store_match:
            vendor = store_match.group(1).strip()
        else:
            vendor = next(
                (
                    line for line in normalized.splitlines()
                    if not re.search(r"\$|\d{1,2}:\d{2}|receipt|authorization", line, re.I)
                ),
                "",
            )

    return {
        "expense_date": expense_date,
        "vendor": vendor,
        "description": description,
        "quantity": quantity,
        "category": category,
        "amount": unit_amount,
        "tax": round(tax, 2),
        "shipping": shipping,
        "total": round(total if total is not None else subtotal + tax + shipping, 2),
        "payment_account": payment_account,
        "receipt_ref": order_number or (receipt_match.group(1) if receipt_match else file_name),
        "notes": f"Scanned from {file_name}",
        "line_items": line_items,
    }


def scan_receipt_pdf(contents: bytes, file_name: str = "receipt.pdf") -> dict[str, Any]:
    """Extract all PDF page text and return proposed expense fields."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF scanning requires the pypdf package.") from exc
    reader = PdfReader(io.BytesIO(contents))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return parse_receipt_text(text, file_name)


def _ordered_ocr_text(results: list[Any]) -> str:
    """Order OCR boxes into visual rows, then left-to-right within each row."""
    if not results:
        return ""
    entries = []
    for box, value, confidence in results:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        entries.append({
            "text": str(value).strip(), "confidence": float(confidence),
            "x": sum(xs) / len(xs), "y": sum(ys) / len(ys),
            "height": max(ys) - min(ys), "width": max(xs) - min(xs),
        })
    tolerance = max(10.0, sorted(entry["height"] for entry in entries)[len(entries) // 2] * 0.65)
    rows: list[list[dict[str, Any]]] = []
    for entry in sorted(entries, key=lambda item: item["y"]):
        row = next((candidate for candidate in rows if abs(candidate[0]["y"] - entry["y"]) <= tolerance), None)
        if row is None:
            rows.append([entry])
        else:
            row.append(entry)
    return "\n".join(" ".join(item["text"] for item in sorted(row, key=lambda item: item["x"])) for row in rows)


def scan_receipt_image(contents: bytes, file_name: str = "receipt.heic") -> dict[str, Any]:
    """Decode a receipt photo, correct orientation, OCR it locally, and parse expense fields."""
    try:
        import numpy as np
        from PIL import Image, ImageOps
        from pillow_heif import register_heif_opener
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError("Image scanning requires pillow-heif and rapidocr-onnxruntime.") from exc
    register_heif_opener()
    with Image.open(io.BytesIO(contents)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    ocr = RapidOCR()
    candidates = []
    for angle in (0, 90, 180, 270):
        rotated = image.rotate(angle, expand=True) if angle else image
        results, _ = ocr(np.asarray(rotated))
        results = results or []
        if not results:
            continue
        horizontal = sum(
            float(confidence) for box, _, confidence in results
            if max(point[0] for point in box) - min(point[0] for point in box)
            >= max(point[1] for point in box) - min(point[1] for point in box)
        )
        confidence = sum(float(item[2]) for item in results)
        candidates.append((horizontal * 2 + confidence, results))
    if not candidates:
        raise ValueError("No readable receipt text was found in the image.")
    _, best_results = max(candidates, key=lambda candidate: candidate[0])
    return parse_receipt_text(_ordered_ocr_text(best_results), Path(file_name).name)
