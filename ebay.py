"""Minimal Sandbox-first client for eBay's OAuth and Inventory APIs."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests


class EbayError(RuntimeError):
    pass


@dataclass(frozen=True)
class EbayConfig:
    environment: str
    client_id: str
    client_secret: str
    refresh_token: str
    access_token: str
    marketplace_id: str
    merchant_location_key: str
    category_id: str
    fulfillment_policy_id: str
    payment_policy_id: str
    return_policy_id: str
    condition: str


class EbayClient:
    INVENTORY_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.inventory"

    def __init__(self, config: EbayConfig, timeout: float = 30.0) -> None:
        self.config = config
        self.timeout = timeout
        sandbox = config.environment.casefold() == "sandbox"
        self.api_base = "https://api.sandbox.ebay.com" if sandbox else "https://api.ebay.com"
        self.token_base = "https://api.sandbox.ebay.com" if sandbox else "https://api.ebay.com"
        self._access_token = config.access_token.strip()

    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        if not (self.config.client_id and self.config.client_secret and self.config.refresh_token):
            raise EbayError("Provide an access token or client ID, client secret, and refresh token.")
        credentials = base64.b64encode(
            f"{self.config.client_id}:{self.config.client_secret}".encode("utf-8")
        ).decode("ascii")
        response = requests.post(
            f"{self.token_base}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.config.refresh_token,
                "scope": self.INVENTORY_SCOPE,
            },
            timeout=self.timeout,
        )
        data = self._json(response)
        self._access_token = str(data.get("access_token", ""))
        if not self._access_token:
            raise EbayError("eBay did not return an access token.")
        return self._access_token

    def _json(self, response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json() if response.content else {}
        except ValueError as exc:
            raise EbayError(f"eBay returned HTTP {response.status_code} with an invalid response.") from exc
        if not response.ok:
            errors = data.get("errors") or []
            message = "; ".join(str(error.get("message", error)) for error in errors)
            raise EbayError(message or str(data) or f"eBay HTTP {response.status_code}")
        return data

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.access_token()}",
            "Content-Type": "application/json",
            "Content-Language": "en-US",
            "Accept-Language": "en-US",
            **kwargs.pop("headers", {}),
        }
        response = requests.request(
            method,
            f"{self.api_base}{path}",
            headers=headers,
            timeout=self.timeout,
            **kwargs,
        )
        return self._json(response)

    def test_connection(self) -> dict[str, Any]:
        return self._request("GET", "/sell/inventory/v1/getVersion")

    def create_inventory_item(self, sku: str, payload: dict[str, Any]) -> None:
        self._request(
            "PUT",
            f"/sell/inventory/v1/inventory_item/{quote(sku, safe='')}",
            json=payload,
        )

    def create_offer(self, payload: dict[str, Any]) -> str:
        data = self._request("POST", "/sell/inventory/v1/offer", json=payload)
        offer_id = str(data.get("offerId", ""))
        if not offer_id:
            raise EbayError("eBay created no offer ID.")
        return offer_id

    def publish_offer(self, offer_id: str) -> str:
        data = self._request(
            "POST",
            f"/sell/inventory/v1/offer/{quote(offer_id, safe='')}/publish",
        )
        return str(data.get("listingId", ""))


def public_image_urls(value: Any) -> list[str]:
    return [
        line.strip()
        for line in str(value or "").splitlines()
        if line.strip().startswith("https://")
    ]


def listing_readiness_issues(card: dict[str, Any], config: EbayConfig | None = None) -> list[str]:
    issues = []
    if not str(card.get("sku") or "").strip():
        issues.append("Missing SKU")
    if not str(card.get("listing_title") or card.get("card_name") or "").strip():
        issues.append("Missing title")
    if len(str(card.get("listing_title") or card.get("card_name") or "")) > 80:
        issues.append("Title exceeds 80 characters")
    if not str(card.get("listing_description") or "").strip():
        issues.append("Missing description")
    if float(card.get("list_price") or 0) <= 0:
        issues.append("Missing price")
    if int(card.get("quantity") or 0) <= 0:
        issues.append("No available quantity")
    if not public_image_urls(card.get("image_urls")):
        issues.append("Missing public HTTPS image")
    if config:
        required_settings = {
            "Marketplace ID": config.marketplace_id,
            "Location key": config.merchant_location_key,
            "Category ID": config.category_id,
            "Fulfillment policy": config.fulfillment_policy_id,
            "Payment policy": config.payment_policy_id,
            "Return policy": config.return_policy_id,
            "Condition": config.condition,
        }
        for label, value in required_settings.items():
            if not str(value or "").strip():
                issues.append(f"Missing eBay {label}")
        if not (
            config.access_token
            or (config.client_id and config.client_secret and config.refresh_token)
        ):
            issues.append("Missing eBay OAuth credentials")
    return issues


def ebay_draft_row(card: dict[str, Any]) -> dict[str, Any]:
    images = public_image_urls(card.get("image_urls"))
    return {
        "Custom label (SKU)": card.get("sku", ""),
        "Title": card.get("listing_title") or card.get("card_name", ""),
        "Description": card.get("listing_description", ""),
        "Quantity": int(card.get("quantity") or 0),
        "Start price": f"{float(card.get('list_price') or 0):.2f}",
        "PicURL": "|".join(images),
        "Set": card.get("set_name", ""),
        "Card Number": card.get("card_number", ""),
        "Condition": card.get("condition", ""),
        "Grader": card.get("grader", ""),
        "Grade": card.get("grade", ""),
        "Certification Number": card.get("certification_number", ""),
    }


def listing_payloads(card: dict[str, Any], config: EbayConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    image_urls = public_image_urls(card.get("image_urls"))
    inventory_item = {
        "availability": {
            "shipToLocationAvailability": {"quantity": int(card.get("quantity") or 1)}
        },
        "condition": config.condition,
        "product": {
            "title": card.get("listing_title") or card.get("card_name", ""),
            "description": card.get("listing_description", ""),
            "imageUrls": image_urls,
            "aspects": {
                "Set": [str(card.get("set_name", ""))],
                "Card Number": [str(card.get("card_number", ""))],
            },
        },
    }
    offer = {
        "sku": card["sku"],
        "marketplaceId": config.marketplace_id,
        "format": "FIXED_PRICE",
        "availableQuantity": int(card.get("quantity") or 1),
        "categoryId": config.category_id,
        "listingDescription": card.get("listing_description", ""),
        "merchantLocationKey": config.merchant_location_key,
        "listingPolicies": {
            "fulfillmentPolicyId": config.fulfillment_policy_id,
            "paymentPolicyId": config.payment_policy_id,
            "returnPolicyId": config.return_policy_id,
        },
        "pricingSummary": {
            "price": {"currency": "USD", "value": f"{float(card.get('list_price') or 0):.2f}"}
        },
    }
    return inventory_item, offer
