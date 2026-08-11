"""Process SportsCardsPro inventory price refreshes in a background thread."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import database
from sportscardspro import (
    SportsCardsProClient,
    build_card_search_query,
    inventory_grade_prices,
    select_product_match,
)


_lock = threading.Lock()
_stop_event = threading.Event()
_thread: threading.Thread | None = None
_status: dict[str, Any] = {
    "state": "idle",
    "total": 0,
    "processed": 0,
    "succeeded": 0,
    "failed": 0,
    "current": "",
    "last_error": "",
    "started_at": "",
    "finished_at": "",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def get_status() -> dict[str, Any]:
    with _lock:
        return dict(_status)


def is_running() -> bool:
    with _lock:
        return bool(_thread and _thread.is_alive())


def start(cards: list[dict[str, Any]], token: str) -> bool:
    """Start a background refresh. Return False if a job is already running."""
    global _thread
    if not cards:
        return False
    with _lock:
        if _thread and _thread.is_alive():
            return False
        _stop_event.clear()
        _status.update({
            "state": "running",
            "total": len(cards),
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "current": "",
            "last_error": "",
            "started_at": _now(),
            "finished_at": "",
        })
        _thread = threading.Thread(
            target=_worker,
            args=(list(cards), token),
            name="price-hunter-refresh",
            daemon=True,
        )
        _thread.start()
    return True


def pause() -> None:
    _stop_event.set()
    with _lock:
        if _status["state"] == "running":
            _status["state"] = "pausing"


def _worker(cards: list[dict[str, Any]], token: str) -> None:
    client = SportsCardsProClient(token)
    for card in cards:
        if _stop_event.is_set():
            with _lock:
                _status.update(state="paused", current="", finished_at=_now())
            return

        with _lock:
            _status["current"] = f"{card.get('sku', '')} — {card.get('card_name', '')}"

        try:
            if card.get("scp_id"):
                product = client.product(card["scp_id"])
            else:
                query = build_card_search_query(card.get("set_name"), card.get("card_name"))
                search = (
                    client.search_pokemon
                    if str(card.get("set_name", "")).casefold().startswith("pokemon")
                    else client.search
                )
                product = select_product_match(card, search(query))
                if product is None:
                    raise ValueError("No unique exact SportsCardsPro match was found.")
                database.update_scp_id(card["id"], str(product["id"]))
            database.update_grade_prices(card["id"], inventory_grade_prices(product))
        except Exception as exc:
            with _lock:
                _status.update(
                    processed=_status["processed"] + 1,
                    failed=_status["failed"] + 1,
                    last_error=f"{card.get('sku', '')}: {exc}",
                )
            continue

        with _lock:
            _status["processed"] += 1
            _status["succeeded"] += 1

    with _lock:
        final_state = "completed_with_errors" if _status["failed"] else "completed"
        _status.update(state=final_state, current="", finished_at=_now())
