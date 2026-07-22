"""Small, rate-limited client for the SportsCardsPro Prices API."""

from __future__ import annotations

import threading
import time
from typing import Any

import requests


class SportsCardsProError(RuntimeError):
    pass


class SportsCardsProClient:
    BASE_URL = "https://www.sportscardspro.com/api"
    _lock = threading.Lock()
    _last_request_at = 0.0

    def __init__(self, token: str, timeout: float = 20.0) -> None:
        if not token.strip():
            raise ValueError("A SportsCardsPro API token is required.")
        self.token = token.strip()
        self.timeout = timeout

    def _get(self, path: str, **params: str) -> dict[str, Any]:
        # SportsCardsPro permits no more than one request per second.
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < 1.05:
                time.sleep(1.05 - elapsed)
            response = requests.get(
                f"{self.BASE_URL}/{path}",
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


def build_card_search_query(*parts: Any) -> str:
    """Build an API search query from independently entered card details."""
    return " ".join(str(part).strip() for part in parts if str(part or "").strip())


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
