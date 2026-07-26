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
                allocated_cost_total REAL,
                market_price REAL,
                graded_8_price REAL,
                graded_9_price REAL,
                psa_10_price REAL,
                grade_prices_refreshed INTEGER NOT NULL DEFAULT 0,
                grade_prices_refreshed_at TEXT,
                list_price REAL,
                storage_location TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Draft',
                ebay_item_id TEXT NOT NULL DEFAULT '',
                ebay_offer_id TEXT NOT NULL DEFAULT '',
                image_urls TEXT NOT NULL DEFAULT '',
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
        if "grade_prices_refreshed_at" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN grade_prices_refreshed_at TEXT")
        if "allocated_cost_total" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN allocated_cost_total REAL")
        if "ebay_offer_id" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN ebay_offer_id TEXT NOT NULL DEFAULT ''")
        if "image_urls" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN image_urls TEXT NOT NULL DEFAULT ''")

        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT UNIQUE,
                purchase_date TEXT,
                description TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                set_name TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                cards_expected INTEGER NOT NULL DEFAULT 0,
                vendor TEXT NOT NULL DEFAULT '',
                purchase_price REAL NOT NULL DEFAULT 0,
                shipping REAL NOT NULL DEFAULT 0,
                tax REAL NOT NULL DEFAULT 0,
                total_cost REAL NOT NULL DEFAULT 0,
                payment_account TEXT NOT NULL DEFAULT '',
                receipt_ref TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Unallocated',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT UNIQUE,
                expense_date TEXT,
                vendor TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                category TEXT NOT NULL DEFAULT '',
                amount REAL NOT NULL DEFAULT 0,
                tax REAL NOT NULL DEFAULT 0,
                shipping REAL NOT NULL DEFAULT 0,
                total REAL NOT NULL DEFAULT 0,
                payment_account TEXT NOT NULL DEFAULT '',
                receipt_ref TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT UNIQUE,
                sale_date TEXT,
                marketplace TEXT NOT NULL DEFAULT 'eBay',
                order_number TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                item_subtotal REAL NOT NULL DEFAULT 0,
                shipping_charged REAL NOT NULL DEFAULT 0,
                fees REAL NOT NULL DEFAULT 0,
                shipping_label_cost REAL NOT NULL DEFAULT 0,
                net_amount REAL NOT NULL DEFAULT 0,
                card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
                cost_of_goods REAL,
                profit REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS purchase_allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                purchase_id INTEGER NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
                card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
                quantity INTEGER NOT NULL,
                base_unit_cost REAL NOT NULL,
                higher_cost_units INTEGER NOT NULL DEFAULT 0,
                allocated_total REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(purchase_id, card_id)
            );

            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(entity_type, entity_id, stored_path)
            );
            """
        )

        sales_existing = {row[1] for row in db.execute("PRAGMA table_info(sales)").fetchall()}
        if "card_id" not in sales_existing:
            db.execute("ALTER TABLE sales ADD COLUMN card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL")
        if "cost_of_goods" not in sales_existing:
            db.execute("ALTER TABLE sales ADD COLUMN cost_of_goods REAL")
        if "profit" not in sales_existing:
            db.execute("ALTER TABLE sales ADD COLUMN profit REAL")


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


def update_cards(item_ids: list[int], values: dict[str, Any]) -> int:
    if not item_ids or not values:
        return 0
    assignments = ", ".join(f"{column} = ?" for column in values)
    placeholders = ", ".join("?" for _ in item_ids)
    with connection() as db:
        cursor = db.execute(
            f"""
            UPDATE cards
            SET {assignments}, updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            (*values.values(), *item_ids),
        )
        return cursor.rowcount


def delete_cards(item_ids: list[int]) -> int:
    if not item_ids:
        return 0
    placeholders = ", ".join("?" for _ in item_ids)
    with connection() as db:
        cursor = db.execute(f"DELETE FROM cards WHERE id IN ({placeholders})", tuple(item_ids))
        return cursor.rowcount


def update_grade_prices(item_id: int, values: dict[str, Any]) -> None:
    assignments = ", ".join(f"{column} = ?" for column in values)
    with connection() as db:
        db.execute(
            f"""
            UPDATE cards
            SET {assignments},
                grade_prices_refreshed = 1,
                grade_prices_refreshed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*values.values(), item_id),
        )


def _insert_many_ignore(table: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    inserted = 0
    with connection() as db:
        for values in rows:
            columns = ", ".join(values)
            placeholders = ", ".join("?" for _ in values)
            cursor = db.execute(
                f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            )
            inserted += cursor.rowcount
    return inserted


def import_purchases(rows: list[dict[str, Any]]) -> int:
    return _insert_many_ignore("purchases", rows)


def import_expenses(rows: list[dict[str, Any]]) -> int:
    return _insert_many_ignore("expenses", rows)


def import_sales(rows: list[dict[str, Any]]) -> int:
    return _insert_many_ignore("sales", rows)


def all_purchases() -> list[dict[str, Any]]:
    with connection() as db:
        rows = db.execute("SELECT * FROM purchases ORDER BY purchase_date DESC, id DESC").fetchall()
    return [dict(row) for row in rows]


def all_expenses() -> list[dict[str, Any]]:
    with connection() as db:
        rows = db.execute("SELECT * FROM expenses ORDER BY expense_date DESC, id DESC").fetchall()
    return [dict(row) for row in rows]


def all_sales() -> list[dict[str, Any]]:
    with connection() as db:
        rows = db.execute("SELECT * FROM sales ORDER BY sale_date DESC, id DESC").fetchall()
    return [dict(row) for row in rows]


def link_sale_to_card(sale_id: int, card_id: int, reduce_inventory: bool = True) -> None:
    with connection() as db:
        sale = db.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        card = db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not sale or not card:
            raise ValueError("Sale or inventory card was not found.")
        sold_quantity = max(1, int(sale["quantity"]))
        unit_cost = float(card["cost"] or 0)
        cost_of_goods = round(unit_cost * sold_quantity, 2)
        profit = round(float(sale["net_amount"] or 0) - cost_of_goods, 2)
        db.execute(
            "UPDATE sales SET card_id = ?, cost_of_goods = ?, profit = ? WHERE id = ?",
            (card_id, cost_of_goods, profit, sale_id),
        )
        if reduce_inventory:
            remaining = max(0, int(card["quantity"]) - sold_quantity)
            status = "Sold" if remaining == 0 else card["status"]
            db.execute(
                """
                UPDATE cards
                SET quantity = ?, status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (remaining, status, card_id),
            )


def auto_match_sales_by_ebay_item_id() -> int:
    with connection() as db:
        matches = db.execute(
            """
            SELECT s.id AS sale_id, c.id AS card_id
            FROM sales s
            JOIN cards c ON c.ebay_item_id != '' AND c.ebay_item_id = s.item_id
            WHERE s.card_id IS NULL
            """
        ).fetchall()
    for match in matches:
        link_sale_to_card(match["sale_id"], match["card_id"], reduce_inventory=False)
    return len(matches)


def all_receipts() -> list[dict[str, Any]]:
    with connection() as db:
        rows = db.execute("SELECT * FROM receipts ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def add_receipt(entity_type: str, entity_id: int, file_name: str, stored_path: str) -> None:
    with connection() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO receipts (entity_type, entity_id, file_name, stored_path)
            VALUES (?, ?, ?, ?)
            """,
            (entity_type, entity_id, file_name, stored_path),
        )


def allocated_card_ids() -> set[int]:
    with connection() as db:
        rows = db.execute("SELECT DISTINCT card_id FROM purchase_allocations").fetchall()
    return {int(row[0]) for row in rows}


def allocate_purchase(purchase_id: int, allocations: list[dict[str, Any]]) -> None:
    """Replace a purchase allocation and update inventory cost fields atomically."""
    with connection() as db:
        previous = db.execute(
            "SELECT card_id FROM purchase_allocations WHERE purchase_id = ?",
            (purchase_id,),
        ).fetchall()
        for row in previous:
            db.execute(
                "UPDATE cards SET allocated_cost_total = NULL WHERE id = ?",
                (row["card_id"],),
            )
        db.execute("DELETE FROM purchase_allocations WHERE purchase_id = ?", (purchase_id,))
        for allocation in allocations:
            db.execute(
                """
                INSERT INTO purchase_allocations (
                    purchase_id, card_id, quantity, base_unit_cost,
                    higher_cost_units, allocated_total
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    purchase_id,
                    allocation["card_id"],
                    allocation["quantity"],
                    allocation["base_unit_cost"],
                    allocation["higher_cost_units"],
                    allocation["allocated_total"],
                ),
            )
            average_cost = round(allocation["allocated_total"] / allocation["quantity"], 2)
            db.execute(
                """
                UPDATE cards
                SET cost = ?, allocated_cost_total = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (average_cost, allocation["allocated_total"], allocation["card_id"]),
            )
        db.execute("UPDATE purchases SET status = 'Allocated' WHERE id = ?", (purchase_id,))


def purchase_allocations(purchase_id: int | None = None) -> list[dict[str, Any]]:
    query = """
        SELECT pa.*, c.sku, c.card_name, p.description AS purchase_description
        FROM purchase_allocations pa
        JOIN cards c ON c.id = pa.card_id
        JOIN purchases p ON p.id = pa.purchase_id
    """
    params: tuple[Any, ...] = ()
    if purchase_id is not None:
        query += " WHERE pa.purchase_id = ?"
        params = (purchase_id,)
    query += " ORDER BY pa.id"
    with connection() as db:
        rows = db.execute(query, params).fetchall()
    return [dict(row) for row in rows]
