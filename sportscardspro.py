"""Small, rate-limited client for the SportsCardsPro Prices API."""

from __future__ import annotations

import threading
import time
import re
import unicodedata
from typing import Any

import requests


class SportsCardsProError(RuntimeError):
    pass


class SportsCardsProClient:
    BASE_URL = "https://www.sportscardspro.com/api"
    POKEMON_BASE_URL = "https://www.pricecharting.com/api"
    _lock = threading.Lock()
    _last_request_at = 0.0

    def __init__(self, token: str, timeout: float = 20.0) -> None:
        if not token.strip():
            raise ValueError("A SportsCardsPro API token is required.")
        self.token = token.strip()
        self.timeout = timeout

    def _get(self, path: str, *, base_url: str | None = None, **params: str) -> dict[str, Any]:
        # SportsCardsPro permits no more than one request per second.
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < 1.05:
                time.sleep(1.05 - elapsed)
            response = requests.get(
                f"{base_url or self.BASE_URL}/{path}",
                params={"t": self.token, **params},
                timeout=self.timeout,
            )
            type(self)._last_request_at = time.monotonic()

        try:
            data = response.json()
        except ValueError as exc:
            raise SportsCardsProError("SportsCardsPro returned an invalid response.") from exc

        if not response.ok or data.get("status") == "error":
            message = data.get("error-message", f"HTTP {response.status_code}")
            raise SportsCardsProError(str(message))
        return data

    def search(self, query: str) -> list[dict[str, Any]]:
        data = self._get("products", q=query.strip())
        return list(data.get("products", []))

    def search_pokemon(self, query: str) -> list[dict[str, Any]]:
        """Search the PriceCharting catalog, which contains Pokemon products."""
        data = self._get("products", base_url=self.POKEMON_BASE_URL, q=query.strip())
        return list(data.get("products", []))

    def product(self, product_id: str) -> dict[str, Any]:
        return self._get("product", id=str(product_id))


def cents_to_dollars(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return int(value) / 100
    except (TypeError, ValueError):
        return None


PRICE_FIELDS = {
    "Raw / Ungraded": "loose-price",
    "Graded 7 / 7.5": "cib-price",
    "Graded 8 / 8.5": "new-price",
    "Graded 9": "graded-price",
    "Graded 9.5": "box-only-price",
    "PSA 10": "manual-only-price",
    "BGS 10": "bgs-10-price",
    "CGC 10": "condition-17-price",
    "SGC 10": "condition-18-price",
}


def available_prices(product: dict[str, Any]) -> dict[str, float]:
    """Return only price guide values that exist and are greater than zero."""
    prices: dict[str, float] = {}
    for label, field in PRICE_FIELDS.items():
        value = cents_to_dollars(product.get(field))
        if value is not None and value > 0:
            prices[label] = value
    return prices


def inventory_grade_prices(product: dict[str, Any]) -> dict[str, float | None]:
    """Return the three grading values displayed in the inventory viewer."""
    return {
        "graded_8_price": cents_to_dollars(product.get("new-price")),
        "graded_9_price": cents_to_dollars(product.get("graded-price")),
        "psa_10_price": cents_to_dollars(product.get("manual-only-price")),
    }


def matches_terms(value: Any, terms: str) -> bool:
    """Return True when every whitespace-separated search term is present."""
    haystack = str(value or "").casefold()
    return all(term.casefold() in haystack for term in terms.split())


def matches_parallel(card_name: Any, terms: str) -> bool:
    """Match parallel terms only within bracketed labels or Alternate Art text."""
    if not terms.strip():
        return True
    name = str(card_name or "")
    variant_labels = re.findall(r"\[([^\]]+)\]", name)
    normalized_terms = terms.casefold().replace("arts", "art")
    if normalized_terms in {"alternate art", "alternate"}:
        return "alternate art" in name.casefold().replace("arts", "art")
    return any(matches_terms(label, terms) for label in variant_labels)


def build_card_search_query(*parts: Any) -> str:
    """Build an API search query from independently entered card details."""
    return " ".join(str(part).strip() for part in parts if str(part or "").strip())


def extract_card_number(product_name: Any) -> str:
    """Extract hash-style or trading-card codes from the end of a product name."""
    name = str(product_name or "").strip()
    hash_match = re.search(r"#([^\s\]]+)\s*$", name)
    if hash_match:
        return hash_match.group(1)
    code_match = re.search(r"\b([A-Z]{1,5}(?:\d{2})?-\d{3})\s*$", name, re.IGNORECASE)
    return code_match.group(1).upper() if code_match else ""


def _identity_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def _product_identity(name: Any, set_name: Any, card_number: Any = "") -> tuple[str, str, str, str]:
    product_name = str(name or "").strip()
    variants = re.findall(r"\[([^]]+)]", product_name)
    variant = variants[-1] if variants else ""
    number = str(card_number or extract_card_number(product_name)).split("/", 1)[0]
    if number.isdigit():
        number = str(int(number))
    base_name = re.sub(
        r"\s+(?:#[^\s]+|[A-Z]{1,5}(?:\d{2})?-\d{3})\s*$", "", product_name,
        flags=re.IGNORECASE,
    )
    base_name = re.sub(r"\s*\[[^]]+]", "", base_name).strip()
    return (
        _identity_text(set_name), _identity_text(base_name),
        _identity_text(number), _identity_text(variant),
    )


def select_product_match(card: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return only an exact set/name/number/variant SportsCardsPro match."""
    target = _product_identity(card.get("card_name"), card.get("set_name"), card.get("card_number"))
    matches = [
        product for product in products
        if _product_identity(product.get("product-name"), product.get("console-name")) == target
    ]
    return matches[0] if len(matches) == 1 else None


def product_price_row(product: dict[str, Any]) -> dict[str, Any]:
    """Flatten a SportsCardsPro product into a table-friendly price row."""
    prices = available_prices(product)
    return {
        "Product ID": str(product.get("id", "")),
        "Card": product.get("product-name", ""),
        "Set": product.get("console-name", ""),
        **{label: prices.get(label) for label in PRICE_FIELDS},
        "Annual sales": product.get("sales-volume"),
    }
