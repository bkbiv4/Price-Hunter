"""SQLite persistence for Price Hunter."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


DB_PATH = Path(__file__).with_name("price_hunter.db")


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def initialize() -> None:
    with connection() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT UNIQUE,
                scp_id TEXT,
                card_name TEXT NOT NULL,
                set_name TEXT NOT NULL DEFAULT '',
                card_number TEXT NOT NULL DEFAULT '',
                condition TEXT NOT NULL DEFAULT 'Ungraded',
                grader TEXT NOT NULL DEFAULT '',
                grade TEXT NOT NULL DEFAULT '',
                certification_number TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 0),
                cost REAL NOT NULL DEFAULT 0,
                market_price REAL,
                graded_8_price REAL,
                graded_9_price REAL,
                psa_10_price REAL,
                grade_prices_refreshed INTEGER NOT NULL DEFAULT 0,
                list_price REAL,
                storage_location TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Draft',
                ebay_item_id TEXT NOT NULL DEFAULT '',
                listing_title TEXT NOT NULL DEFAULT '',
                listing_description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        existing = {row[1] for row in db.execute("PRAGMA table_info(cards)").fetchall()}
        if "grader" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN grader TEXT NOT NULL DEFAULT ''")
        if "certification_number" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN certification_number TEXT NOT NULL DEFAULT ''")
        if "graded_8_price" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN graded_8_price REAL")
        if "graded_9_price" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN graded_9_price REAL")
        if "psa_10_price" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN psa_10_price REAL")
        if "grade_prices_refreshed" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN grade_prices_refreshed INTEGER NOT NULL DEFAULT 0")


def add_card(values: dict[str, Any]) -> int:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    with connection() as db:
        cursor = db.execute(
            f"INSERT INTO cards ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        )
        item_id = int(cursor.lastrowid)
        sku = f"PH-{item_id:06d}"
        db.execute("UPDATE cards SET sku = ? WHERE id = ?", (sku, item_id))
        return item_id


def add_cards(rows: list[dict[str, Any]]) -> int:
    """Insert multiple inventory rows in one transaction."""
    if not rows:
        return 0
    with connection() as db:
        for values in rows:
            columns = ", ".join(values)
            placeholders = ", ".join("?" for _ in values)
            cursor = db.execute(
                f"INSERT INTO cards ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
            item_id = int(cursor.lastrowid)
            db.execute("UPDATE cards SET sku = ? WHERE id = ?", (f"PH-{item_id:06d}", item_id))
    return len(rows)


def all_cards() -> list[dict[str, Any]]:
    with connection() as db:
        rows = db.execute("SELECT * FROM cards ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def get_card(item_id: int) -> dict[str, Any] | None:
    with connection() as db:
        row = db.execute("SELECT * FROM cards WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def update_card(item_id: int, values: dict[str, Any]) -> None:
    assignments = ", ".join(f"{column} = ?" for column in values)
    with connection() as db:
        db.execute(
            f"UPDATE cards SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*values.values(), item_id),
        )
