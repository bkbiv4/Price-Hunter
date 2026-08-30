"""SQLite persistence for Price Hunter."""

from __future__ import annotations

import csv
import sqlite3
import shutil
import zipfile
from contextlib import contextmanager
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Iterator


DB_PATH = Path(__file__).with_name("price_hunter.db")
CORE_TABLES = [
    "cards",
    "purchases",
    "expenses",
    "sales",
    "purchase_allocations",
    "receipts",
    "grading_submissions",
    "grading_items",
    "inventory_events",
    "sale_items",
]


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


def database_path() -> Path:
    return DB_PATH


def database_exists() -> bool:
    return DB_PATH.exists()


def table_counts() -> dict[str, int]:
    if not DB_PATH.exists():
        return {table: 0 for table in CORE_TABLES}
    counts: dict[str, int] = {}
    with connection() as db:
        existing = {
            row["name"]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for table in CORE_TABLES:
            if table in existing:
                counts[table] = int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            else:
                counts[table] = 0
    return counts


def backup_bytes() -> bytes:
    if not DB_PATH.exists():
        return b""
    return DB_PATH.read_bytes()


def export_csv_zip() -> bytes:
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        with connection() as db:
            existing = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table in CORE_TABLES:
                if table not in existing:
                    continue
                rows = db.execute(f"SELECT * FROM {table}").fetchall()
                columns = [description[0] for description in db.execute(f"SELECT * FROM {table} LIMIT 0").description]
                output = StringIO()
                writer = csv.DictWriter(output, fieldnames=columns)
                writer.writeheader()
                for row in rows:
                    writer.writerow({column: row[column] for column in columns})
                bundle.writestr(f"{table}.csv", output.getvalue())
    return archive.getvalue()


def _validate_database(path: Path) -> None:
    try:
        db = sqlite3.connect(path)
        try:
            result = db.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise ValueError("SQLite integrity check failed.")
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            required = {"cards", "sales", "purchases", "expenses"}
            missing = required - tables
            if missing:
                raise ValueError(f"Backup is missing required table(s): {', '.join(sorted(missing))}.")
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise ValueError("Uploaded file is not a readable SQLite database.") from exc


def restore_database(contents: bytes) -> Path | None:
    if not contents:
        raise ValueError("Uploaded database file is empty.")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    candidate = DB_PATH.with_suffix(".upload.db")
    candidate.write_bytes(contents)
    try:
        _validate_database(candidate)
        backup_path = None
        if DB_PATH.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = DB_PATH.with_name(f"{DB_PATH.stem}.before-restore-{stamp}{DB_PATH.suffix}")
            shutil.copy2(DB_PATH, backup_path)
        shutil.move(str(candidate), DB_PATH)
        initialize()
        return backup_path
    finally:
        if candidate.exists():
            candidate.unlink()


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
                grading_status TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 0),
                cost REAL NOT NULL DEFAULT 0,
                grading_cost REAL NOT NULL DEFAULT 0,
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
        if "grading_status" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN grading_status TEXT NOT NULL DEFAULT ''")
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
        if "grading_cost" not in existing:
            db.execute("ALTER TABLE cards ADD COLUMN grading_cost REAL NOT NULL DEFAULT 0")
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
                sku TEXT NOT NULL DEFAULT '',
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
                allocation_group TEXT NOT NULL DEFAULT '',
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

            CREATE TABLE IF NOT EXISTS grading_submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_number TEXT NOT NULL DEFAULT '',
                grader TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Preparing',
                submitted_date TEXT,
                returned_date TEXT,
                grading_fee REAL NOT NULL DEFAULT 0,
                shipping_cost REAL NOT NULL DEFAULT 0,
                insurance_cost REAL NOT NULL DEFAULT 0,
                other_cost REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS grading_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER NOT NULL REFERENCES grading_submissions(id) ON DELETE CASCADE,
                card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                grade TEXT NOT NULL DEFAULT '',
                certification_number TEXT NOT NULL DEFAULT '',
                allocated_grading_cost REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(submission_id, card_id)
            );

            CREATE TABLE IF NOT EXISTS inventory_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
                card_sku TEXT NOT NULL DEFAULT '',
                card_name TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                quantity_change INTEGER NOT NULL DEFAULT 0,
                amount REAL,
                reference_type TEXT NOT NULL DEFAULT '',
                reference_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sale_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
                card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
                card_sku TEXT NOT NULL DEFAULT '',
                card_name TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                unit_price REAL NOT NULL DEFAULT 0,
                unit_cost REAL NOT NULL DEFAULT 0,
                acquisition_unit_cost REAL NOT NULL DEFAULT 0,
                grading_unit_cost REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        for column, definition in {
            "buyer": "TEXT NOT NULL DEFAULT ''",
            "payment_method": "TEXT NOT NULL DEFAULT ''",
            "cash_received": "REAL NOT NULL DEFAULT 0",
            "trade_value": "REAL NOT NULL DEFAULT 0",
            "tax_collected": "REAL NOT NULL DEFAULT 0",
            "promoted_listing_fees": "REAL NOT NULL DEFAULT 0",
            "status": "TEXT NOT NULL DEFAULT 'Completed'",
            "notes": "TEXT NOT NULL DEFAULT ''",
            "gross_net_amount": "REAL",
            "refunded_amount": "REAL NOT NULL DEFAULT 0",
            "additional_expenses": "REAL NOT NULL DEFAULT 0",
            "return_shipping_cost": "REAL NOT NULL DEFAULT 0",
            "inventory_restored": "INTEGER NOT NULL DEFAULT 0",
            "sku": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if column not in sales_existing:
                db.execute(f"ALTER TABLE sales ADD COLUMN {column} {definition}")
        db.execute(
            "UPDATE sales SET gross_net_amount = net_amount WHERE gross_net_amount IS NULL"
        )
        allocation_existing = {
            row[1] for row in db.execute("PRAGMA table_info(purchase_allocations)").fetchall()
        }
        if "allocation_group" not in allocation_existing:
            db.execute(
                "ALTER TABLE purchase_allocations ADD COLUMN allocation_group TEXT NOT NULL DEFAULT ''"
            )
        sale_item_existing = {
            row[1] for row in db.execute("PRAGMA table_info(sale_items)").fetchall()
        }
        added_cost_components = "acquisition_unit_cost" not in sale_item_existing
        if added_cost_components:
            db.execute(
                "ALTER TABLE sale_items ADD COLUMN acquisition_unit_cost REAL NOT NULL DEFAULT 0"
            )
            db.execute(
                "ALTER TABLE sale_items ADD COLUMN grading_unit_cost REAL NOT NULL DEFAULT 0"
            )
            db.execute(
                """
                UPDATE sale_items
                SET grading_unit_cost = COALESCE((
                        SELECT grading_cost FROM cards WHERE cards.id = sale_items.card_id
                    ), 0),
                    acquisition_unit_cost = CASE
                        WHEN unit_cost >= COALESCE((
                            SELECT grading_cost FROM cards WHERE cards.id = sale_items.card_id
                        ), 0)
                        THEN unit_cost - COALESCE((
                            SELECT grading_cost FROM cards WHERE cards.id = sale_items.card_id
                        ), 0)
                        ELSE unit_cost
                    END
                """
            )
            db.execute(
                "UPDATE sale_items SET unit_cost = acquisition_unit_cost + grading_unit_cost"
            )
            _recalculate_all_sale_totals(db)


def _record_event(
    db: sqlite3.Connection,
    card_id: int | None,
    event_type: str,
    details: str = "",
    quantity_change: int = 0,
    amount: float | None = None,
    reference_type: str = "",
    reference_id: int | None = None,
    card_snapshot: sqlite3.Row | dict[str, Any] | None = None,
) -> None:
    card = card_snapshot
    if card is None and card_id is not None:
        card = db.execute("SELECT sku, card_name FROM cards WHERE id = ?", (card_id,)).fetchone()
    db.execute(
        """
        INSERT INTO inventory_events (
            card_id, card_sku, card_name, event_type, details, quantity_change,
            amount, reference_type, reference_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            card_id,
            card["sku"] if card else "",
            card["card_name"] if card else "",
            event_type,
            details,
            quantity_change,
            amount,
            reference_type,
            reference_id,
        ),
    )


def _recalculate_sale_totals(db: sqlite3.Connection, sale_ids: list[int]) -> None:
    for sale_id in sorted(set(sale_ids)):
        sale = db.execute("SELECT net_amount FROM sales WHERE id = ?", (sale_id,)).fetchone()
        if not sale:
            continue
        cogs = round(float(db.execute(
            "SELECT COALESCE(SUM(unit_cost * quantity), 0) FROM sale_items WHERE sale_id = ?",
            (sale_id,),
        ).fetchone()[0]), 2)
        db.execute(
            "UPDATE sales SET cost_of_goods = ?, profit = ? WHERE id = ?",
            (cogs, round(float(sale["net_amount"] or 0) - cogs, 2), sale_id),
        )


def _recalculate_all_sale_totals(db: sqlite3.Connection) -> None:
    _recalculate_sale_totals(
        db, [int(row[0]) for row in db.execute("SELECT id FROM sales").fetchall()]
    )


def _refresh_card_grading_cost_in_sales(db: sqlite3.Connection, card_id: int) -> None:
    card = db.execute("SELECT grading_cost FROM cards WHERE id = ?", (card_id,)).fetchone()
    if not card:
        return
    sale_ids = [int(row[0]) for row in db.execute(
        "SELECT DISTINCT sale_id FROM sale_items WHERE card_id = ?", (card_id,)
    ).fetchall()]
    grading_cost = float(card["grading_cost"] or 0)
    db.execute(
        """
        UPDATE sale_items SET grading_unit_cost = ?,
            unit_cost = acquisition_unit_cost + ? WHERE card_id = ?
        """,
        (grading_cost, grading_cost, card_id),
    )
    _recalculate_sale_totals(db, sale_ids)


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
        _record_event(db, item_id, "Acquired", "Added to inventory", int(values.get("quantity", 1)))
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
            _record_event(db, item_id, "Acquired", "Imported into inventory", int(values.get("quantity", 1)))
    return len(rows)


def split_card(item_id: int, split_quantity: int, split_values: dict[str, Any]) -> int:
    """Move part of an inventory row into a new row while preserving the remainder."""
    with connection() as db:
        card = db.execute("SELECT * FROM cards WHERE id = ?", (item_id,)).fetchone()
        if not card:
            raise ValueError("Inventory card was not found.")
        original_quantity = int(card["quantity"])
        split_quantity = int(split_quantity)
        if split_quantity < 1 or split_quantity >= original_quantity:
            raise ValueError("Split quantity must leave at least one card in the original row.")
        if db.execute(
            "SELECT 1 FROM purchase_allocations WHERE card_id = ? LIMIT 1", (item_id,)
        ).fetchone():
            raise ValueError(
                "This row has purchase allocations. Remove or revise its allocation before splitting it."
            )
        excluded = {"id", "sku", "created_at", "updated_at", "allocated_cost_total"}
        values = {key: card[key] for key in card.keys() if key not in excluded}
        values.update(split_values)
        values["quantity"] = split_quantity
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = db.execute(
            f"INSERT INTO cards ({columns}) VALUES ({placeholders})", tuple(values.values())
        )
        new_id = int(cursor.lastrowid)
        new_sku = f"PH-{new_id:06d}"
        db.execute("UPDATE cards SET sku = ? WHERE id = ?", (new_sku, new_id))
        remainder = original_quantity - split_quantity
        db.execute(
            "UPDATE cards SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (remainder, item_id),
        )
        _record_event(
            db, item_id, "Quantity split",
            f"Moved {split_quantity} card(s) to {new_sku}; {remainder} remain",
            -split_quantity, reference_type="inventory_split", reference_id=new_id,
        )
        _record_event(
            db, new_id, "Split from inventory row",
            f"Created from {card['sku']} with quantity {split_quantity}",
            split_quantity, reference_type="inventory_split", reference_id=item_id,
        )
        return new_id


def clone_card(item_id: int, quantity: int, overrides: dict[str, Any] | None = None) -> int:
    """Create another inventory row from an existing card's saved details."""
    card = get_card(item_id)
    if not card:
        raise ValueError("Inventory card was not found.")
    excluded = {"id", "sku", "created_at", "updated_at", "allocated_cost_total"}
    values = {key: value for key, value in card.items() if key not in excluded}
    values.update(overrides or {})
    values["quantity"] = int(quantity)
    return add_card(values)


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
        before = db.execute("SELECT * FROM cards WHERE id = ?", (item_id,)).fetchone()
        db.execute(
            f"UPDATE cards SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*values.values(), item_id),
        )
        if "grading_cost" in values:
            _refresh_card_grading_cost_in_sales(db, item_id)
        if before:
            changes = [
                f"{column}: {before[column]} → {value}"
                for column, value in values.items()
                if column in before.keys() and before[column] != value
            ]
            if changes:
                quantity_change = int(values.get("quantity", before["quantity"])) - int(before["quantity"])
                _record_event(db, item_id, "Updated", "; ".join(changes), quantity_change)


def update_cards(item_ids: list[int], values: dict[str, Any]) -> int:
    if not item_ids or not values:
        return 0
    assignments = ", ".join(f"{column} = ?" for column in values)
    placeholders = ", ".join("?" for _ in item_ids)
    with connection() as db:
        before_rows = db.execute(
            f"SELECT * FROM cards WHERE id IN ({placeholders})", tuple(item_ids)
        ).fetchall()
        cursor = db.execute(
            f"""
            UPDATE cards
            SET {assignments}, updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            (*values.values(), *item_ids),
        )
        if "grading_cost" in values:
            for item_id in item_ids:
                _refresh_card_grading_cost_in_sales(db, int(item_id))
        for before in before_rows:
            changes = [
                f"{column}: {before[column]} → {value}"
                for column, value in values.items()
                if column in before.keys() and before[column] != value
            ]
            if changes:
                quantity_change = int(values.get("quantity", before["quantity"])) - int(before["quantity"])
                _record_event(db, int(before["id"]), "Bulk updated", "; ".join(changes), quantity_change)
        return cursor.rowcount


def delete_cards(item_ids: list[int]) -> int:
    if not item_ids:
        return 0
    placeholders = ", ".join("?" for _ in item_ids)
    with connection() as db:
        rows = db.execute(f"SELECT * FROM cards WHERE id IN ({placeholders})", tuple(item_ids)).fetchall()
        for row in rows:
            _record_event(
                db, int(row["id"]), "Deleted", "Inventory record deleted",
                -int(row["quantity"]), card_snapshot=row,
            )
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


def update_scp_id(item_id: int, scp_id: str) -> None:
    """Save a resolved SportsCardsPro ID without creating a noisy inventory edit event."""
    with connection() as db:
        db.execute(
            "UPDATE cards SET scp_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (str(scp_id), item_id),
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


def add_purchase(values: dict[str, Any]) -> int:
    """Create one manually entered purchase and return its ID."""
    allowed = {
        "purchase_date", "description", "category", "set_name", "quantity",
        "cards_expected", "vendor", "purchase_price", "shipping", "tax",
        "total_cost", "payment_account", "receipt_ref", "notes", "status",
    }
    row = {column: value for column, value in values.items() if column in allowed}
    if not str(row.get("description", "")).strip():
        raise ValueError("Purchase description is required.")
    with connection() as db:
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        cursor = db.execute(
            f"INSERT INTO purchases ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        return int(cursor.lastrowid)


def add_expense(values: dict[str, Any]) -> int:
    """Create one manually entered expense and return its ID."""
    allowed = {
        "expense_date", "vendor", "description", "quantity", "category",
        "amount", "tax", "shipping", "total", "payment_account",
        "receipt_ref", "notes",
    }
    row = {column: value for column, value in values.items() if column in allowed}
    if not str(row.get("description", "")).strip():
        raise ValueError("Expense description is required.")
    with connection() as db:
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        cursor = db.execute(
            f"INSERT INTO expenses ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        return int(cursor.lastrowid)


def update_purchase(purchase_id: int, values: dict[str, Any]) -> None:
    """Update the editable fields of an existing purchase."""
    allowed = {
        "purchase_date", "description", "category", "set_name", "quantity",
        "cards_expected", "vendor", "purchase_price", "shipping", "tax",
        "total_cost", "payment_account", "receipt_ref", "notes",
    }
    row = {column: value for column, value in values.items() if column in allowed}
    if not str(row.get("description", "")).strip():
        raise ValueError("Purchase description is required.")
    if not row:
        return
    assignments = ", ".join(f"{column} = ?" for column in row)
    with connection() as db:
        cursor = db.execute(
            f"UPDATE purchases SET {assignments} WHERE id = ?",
            (*row.values(), purchase_id),
        )
        if cursor.rowcount == 0:
            raise ValueError("Purchase was not found.")


def import_sales(rows: list[dict[str, Any]]) -> int:
    inserted = _insert_many_ignore("sales", rows)
    with connection() as db:
        for row in rows:
            if not row.get("source_key"):
                continue
            db.execute(
                """
                UPDATE sales SET
                    sku = CASE WHEN ? != '' THEN ? ELSE sku END,
                    item_id = CASE WHEN ? != '' THEN ? ELSE item_id END,
                    title = CASE WHEN ? != '' THEN ? ELSE title END
                WHERE source_key = ?
                """,
                (
                    row.get("sku", ""), row.get("sku", ""),
                    row.get("item_id", ""), row.get("item_id", ""),
                    row.get("title", ""), row.get("title", ""),
                    row["source_key"],
                ),
            )
    return inserted


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


def update_sale(sale_id: int, values: dict[str, Any]) -> None:
    """Update sale details and recalculate net proceeds and profit."""
    allowed = {
        "sale_date", "marketplace", "order_number", "item_id", "sku", "title",
        "buyer", "payment_method", "cash_received", "trade_value", "item_subtotal",
        "shipping_charged", "tax_collected", "fees", "promoted_listing_fees",
        "shipping_label_cost", "notes",
    }
    edits = {column: value for column, value in values.items() if column in allowed}
    if not str(edits.get("title", "")).strip():
        raise ValueError("Sale title is required.")
    with connection() as db:
        current = db.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        if not current:
            raise ValueError("Sale was not found.")
        merged = {**dict(current), **edits}
        base_net = round(
            float(merged.get("item_subtotal") or 0)
            + float(merged.get("shipping_charged") or 0)
            - float(merged.get("fees") or 0)
            - float(merged.get("promoted_listing_fees") or 0)
            - float(merged.get("shipping_label_cost") or 0),
            2,
        )
        adjusted_net = round(
            base_net
            - float(merged.get("refunded_amount") or 0)
            - float(merged.get("additional_expenses") or 0)
            - float(merged.get("return_shipping_cost") or 0),
            2,
        )
        cost_of_goods = merged.get("cost_of_goods")
        profit = round(adjusted_net - float(cost_of_goods), 2) if cost_of_goods is not None else None
        edits.update({"gross_net_amount": base_net, "net_amount": adjusted_net, "profit": profit})
        assignments = ", ".join(f"{column} = ?" for column in edits)
        db.execute(
            f"UPDATE sales SET {assignments} WHERE id = ?",
            (*edits.values(), sale_id),
        )


def delete_sale(sale_id: int, restore_inventory: bool = False) -> None:
    """Delete a sale, optionally restoring its sale-item quantities to inventory."""
    with connection() as db:
        sale = db.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        if not sale:
            raise ValueError("Sale was not found.")
        if restore_inventory and not bool(sale["inventory_restored"]):
            items = db.execute(
                "SELECT * FROM sale_items WHERE sale_id = ?", (sale_id,)
            ).fetchall()
            for item in items:
                if item["card_id"] is None:
                    continue
                card = db.execute("SELECT * FROM cards WHERE id = ?", (item["card_id"],)).fetchone()
                if not card:
                    continue
                db.execute(
                    """
                    UPDATE cards SET quantity = quantity + ?, status = 'Ready',
                        updated_at = CURRENT_TIMESTAMP WHERE id = ?
                    """,
                    (int(item["quantity"]), item["card_id"]),
                )
                _record_event(
                    db, int(item["card_id"]), "Sale deleted - inventory restored",
                    f"Deleted sale #{sale_id}", int(item["quantity"]),
                )
        db.execute("DELETE FROM sales WHERE id = ?", (sale_id,))


def link_sale_to_card(sale_id: int, card_id: int, reduce_inventory: bool = True) -> None:
    with connection() as db:
        sale = db.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        card = db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not sale or not card:
            raise ValueError("Sale or inventory card was not found.")
        sold_quantity = max(1, int(sale["quantity"]))
        acquisition_cost = float(card["cost"] or 0)
        grading_cost = float(card["grading_cost"] or 0)
        unit_cost = acquisition_cost + grading_cost
        cost_of_goods = round(unit_cost * sold_quantity, 2)
        profit = round(float(sale["net_amount"] or 0) - cost_of_goods, 2)
        db.execute(
            "UPDATE sales SET card_id = ?, cost_of_goods = ?, profit = ? WHERE id = ?",
            (card_id, cost_of_goods, profit, sale_id),
        )
        db.execute(
            """
            INSERT INTO sale_items (
                sale_id, card_id, card_sku, card_name, quantity, unit_price, unit_cost,
                acquisition_unit_cost, grading_unit_cost
            )
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM sale_items WHERE sale_id = ? AND card_id = ?
            )
            """,
            (
                sale_id, card_id, card["sku"], card["card_name"], sold_quantity,
                round(float(sale["item_subtotal"] or 0) / sold_quantity, 2), unit_cost,
                acquisition_cost, grading_cost,
                sale_id, card_id,
            ),
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
            _record_event(
                db, card_id, "Sold",
                f"Linked imported sale {sale['order_number']}; net ${float(sale['net_amount'] or 0):.2f}",
                -sold_quantity, float(sale["net_amount"] or 0), "sale", sale_id,
            )


def auto_match_sales_by_ebay_item_id() -> int:
    return auto_match_ebay_sales()["matched"]


def auto_match_ebay_sales() -> dict[str, int]:
    """Match unmatched eBay rows by unique item ID first, then unique Price Hunter SKU."""
    with connection() as db:
        sales = db.execute(
            """
            SELECT id, item_id, sku FROM sales
            WHERE card_id IS NULL AND marketplace = 'eBay'
            """
        ).fetchall()
        matches: list[tuple[int, int]] = []
        ambiguous = 0
        for sale in sales:
            candidates: list[sqlite3.Row] = []
            if sale["item_id"]:
                candidates = db.execute(
                    "SELECT id FROM cards WHERE ebay_item_id = ?", (sale["item_id"],)
                ).fetchall()
            if not candidates and sale["sku"]:
                candidates = db.execute(
                    "SELECT id FROM cards WHERE UPPER(sku) = UPPER(?)", (sale["sku"].strip(),)
                ).fetchall()
            unique_ids = {int(row["id"]) for row in candidates}
            if len(unique_ids) == 1:
                matches.append((int(sale["id"]), unique_ids.pop()))
            elif len(unique_ids) > 1:
                ambiguous += 1
    for sale_id, card_id in matches:
        link_sale_to_card(sale_id, card_id, reduce_inventory=False)
    return {
        "matched": len(matches),
        "ambiguous": ambiguous,
        "unmatched": max(0, len(sales) - len(matches)),
    }


def create_manual_sale(card_id: int, values: dict[str, Any]) -> int:
    """Create a completed sale, calculate profit, and reduce inventory atomically."""
    with connection() as db:
        card = db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        if not card:
            raise ValueError("Inventory card was not found.")
        sold_quantity = int(values.get("quantity", 1))
        if sold_quantity < 1 or sold_quantity > int(card["quantity"]):
            raise ValueError("Sale quantity must be between 1 and the available quantity.")
        item_subtotal = round(float(values.get("item_subtotal", 0)), 2)
        shipping_charged = round(float(values.get("shipping_charged", 0)), 2)
        fees = round(float(values.get("fees", 0)), 2)
        promoted_fees = round(float(values.get("promoted_listing_fees", 0)), 2)
        shipping_label = round(float(values.get("shipping_label_cost", 0)), 2)
        net_amount = round(item_subtotal + shipping_charged - fees - promoted_fees - shipping_label, 2)
        unit_cost = float(card["cost"] or 0) + float(card["grading_cost"] or 0)
        cost_of_goods = round(unit_cost * sold_quantity, 2)
        profit = round(net_amount - cost_of_goods, 2)
        sale_values = {
            **values,
            "card_id": card_id,
            "net_amount": net_amount,
            "gross_net_amount": net_amount,
            "cost_of_goods": cost_of_goods,
            "profit": profit,
        }
        columns = ", ".join(sale_values)
        placeholders = ", ".join("?" for _ in sale_values)
        cursor = db.execute(
            f"INSERT INTO sales ({columns}) VALUES ({placeholders})",
            tuple(sale_values.values()),
        )
        sale_id = int(cursor.lastrowid)
        db.execute(
            """
            INSERT INTO sale_items (
                sale_id, card_id, card_sku, card_name, quantity, unit_price, unit_cost,
                acquisition_unit_cost, grading_unit_cost
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sale_id, card_id, card["sku"], card["card_name"], sold_quantity,
                round(item_subtotal / sold_quantity, 2), unit_cost,
                float(card["cost"] or 0), float(card["grading_cost"] or 0),
            ),
        )
        remaining = int(card["quantity"]) - sold_quantity
        db.execute(
            """
            UPDATE cards SET quantity = ?, status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (remaining, "Sold" if remaining == 0 else card["status"], card_id),
        )
        _record_event(
            db, card_id, "Sold",
            f"{values.get('marketplace', 'Other')} sale; "
            f"payment {values.get('payment_method', 'Unspecified')}; "
            f"net ${net_amount:.2f}; profit ${profit:.2f}",
            -sold_quantity, net_amount, "sale", sale_id,
        )
        return sale_id


def create_multi_card_sale(values: dict[str, Any], items: list[dict[str, Any]]) -> int:
    if not items:
        raise ValueError("Choose at least one inventory card.")
    with connection() as db:
        prepared = []
        item_subtotal = 0.0
        cost_of_goods = 0.0
        total_quantity = 0
        for item in items:
            card = db.execute("SELECT * FROM cards WHERE id = ?", (int(item["card_id"]),)).fetchone()
            quantity = int(item.get("quantity", 1))
            unit_price = round(float(item.get("unit_price", 0)), 2)
            if not card or quantity < 1 or quantity > int(card["quantity"]):
                raise ValueError("A sale quantity exceeds available inventory.")
            prepared.append((card, quantity, unit_price))
            total_quantity += quantity
            item_subtotal += unit_price * quantity
            cost_of_goods += (
                float(card["cost"] or 0) + float(card["grading_cost"] or 0)
            ) * quantity
        shipping_charged = float(values.get("shipping_charged", 0) or 0)
        fees = float(values.get("fees", 0) or 0)
        promoted_fees = float(values.get("promoted_listing_fees", 0) or 0)
        label_cost = float(values.get("shipping_label_cost", 0) or 0)
        item_subtotal = round(item_subtotal, 2)
        cost_of_goods = round(cost_of_goods, 2)
        net_amount = round(item_subtotal + shipping_charged - fees - promoted_fees - label_cost, 2)
        profit = round(net_amount - cost_of_goods, 2)
        sale_values = {
            **values,
            "title": values.get("title") or f"{len(prepared)}-card lot",
            "quantity": total_quantity,
            "item_subtotal": item_subtotal,
            "net_amount": net_amount,
            "gross_net_amount": net_amount,
            "cost_of_goods": cost_of_goods,
            "profit": profit,
        }
        columns = ", ".join(sale_values)
        placeholders = ", ".join("?" for _ in sale_values)
        cursor = db.execute(
            f"INSERT INTO sales ({columns}) VALUES ({placeholders})",
            tuple(sale_values.values()),
        )
        sale_id = int(cursor.lastrowid)
        for card, quantity, unit_price in prepared:
            db.execute(
                """
                INSERT INTO sale_items (
                    sale_id, card_id, card_sku, card_name, quantity, unit_price, unit_cost,
                    acquisition_unit_cost, grading_unit_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sale_id, card["id"], card["sku"], card["card_name"], quantity,
                    unit_price,
                    float(card["cost"] or 0) + float(card["grading_cost"] or 0),
                    float(card["cost"] or 0), float(card["grading_cost"] or 0),
                ),
            )
            remaining = int(card["quantity"]) - quantity
            db.execute(
                "UPDATE cards SET quantity = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (remaining, "Sold" if remaining == 0 else card["status"], card["id"]),
            )
            _record_event(
                db, int(card["id"]), "Sold in lot",
                f"Sale #{sale_id}; {values.get('marketplace', 'Other')}; unit price ${unit_price:.2f}",
                -quantity, round(unit_price * quantity, 2), "sale", sale_id,
            )
        return sale_id


def sale_items(sale_id: int) -> list[dict[str, Any]]:
    with connection() as db:
        rows = db.execute(
            "SELECT * FROM sale_items WHERE sale_id = ? ORDER BY id", (sale_id,)
        ).fetchall()
    return [dict(row) for row in rows]


def update_sale_adjustment(
    sale_id: int,
    status: str,
    refunded_amount: float,
    additional_expenses: float,
    return_shipping_cost: float,
    notes: str,
    restore_inventory: bool = False,
) -> None:
    with connection() as db:
        sale = db.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        if not sale:
            raise ValueError("Sale was not found.")
        refunded_amount = round(max(0, refunded_amount), 2)
        additional_expenses = round(max(0, additional_expenses), 2)
        return_shipping_cost = round(max(0, return_shipping_cost), 2)
        gross_net = float(sale["gross_net_amount"] or sale["net_amount"] or 0)
        adjusted_net = round(
            gross_net - refunded_amount - additional_expenses - return_shipping_cost, 2
        )
        should_restore = (
            restore_inventory
            and status in {"Returned", "Cancelled"}
            and not bool(sale["inventory_restored"])
        )
        adjusted_cogs = (
            0.0 if should_restore or bool(sale["inventory_restored"])
            else float(sale["cost_of_goods"] or 0)
        )
        profit = round(adjusted_net - adjusted_cogs, 2)
        if should_restore:
            items = db.execute(
                "SELECT * FROM sale_items WHERE sale_id = ?", (sale_id,)
            ).fetchall()
            if not items and sale["card_id"] is not None:
                card = db.execute("SELECT * FROM cards WHERE id = ?", (sale["card_id"],)).fetchone()
                if card:
                    items = [{
                        "card_id": sale["card_id"],
                        "quantity": sale["quantity"],
                        "card_sku": card["sku"],
                        "card_name": card["card_name"],
                    }]
            for item in items:
                if item["card_id"] is None:
                    continue
                card = db.execute("SELECT * FROM cards WHERE id = ?", (item["card_id"],)).fetchone()
                if not card:
                    continue
                db.execute(
                    """
                    UPDATE cards SET quantity = quantity + ?, status = 'Ready',
                        updated_at = CURRENT_TIMESTAMP WHERE id = ?
                    """,
                    (int(item["quantity"]), item["card_id"]),
                )
                _record_event(
                    db, int(item["card_id"]), "Returned to inventory",
                    f"{status} sale #{sale_id}", int(item["quantity"]),
                    None, "sale", sale_id,
                )
        db.execute(
            """
            UPDATE sales SET status = ?, refunded_amount = ?, additional_expenses = ?,
                return_shipping_cost = ?, net_amount = ?, cost_of_goods = ?, profit = ?, notes = ?,
                inventory_restored = CASE WHEN ? THEN 1 ELSE inventory_restored END
            WHERE id = ?
            """,
            (
                status, refunded_amount, additional_expenses, return_shipping_cost,
                adjusted_net, adjusted_cogs, profit, notes, 1 if should_restore else 0, sale_id,
            ),
        )

#47332e92d50a8d360bbd35f2234218aa75e555d2
def create_grading_submission(values: dict[str, Any], items: list[dict[str, Any]]) -> int:
    if not items:
        raise ValueError("Choose at least one card for the grading submission.")
    with connection() as db:
        total_units = sum(int(item.get("quantity", 1)) for item in items)
        if total_units < 1:
            raise ValueError("Submission quantity must be at least one.")
        total_cost = round(
            sum(float(values.get(field, 0) or 0) for field in (
                "grading_fee", "shipping_cost", "insurance_cost", "other_cost"
            )),
            2,
        )
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = db.execute(
            f"INSERT INTO grading_submissions ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        )
        submission_id = int(cursor.lastrowid)
        per_unit = round(total_cost / total_units, 2)
        allocated = 0.0
        for position, item in enumerate(items):
            card_id = int(item["card_id"])
            quantity = int(item.get("quantity", 1))
            card = db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
            if not card or quantity > int(card["quantity"]):
                raise ValueError("A submitted quantity exceeds available inventory.")
            item_cost = round(per_unit * quantity, 2)
            if position == len(items) - 1:
                item_cost = round(total_cost - allocated, 2)
            allocated += item_cost
            db.execute(
                """
                INSERT INTO grading_items (
                    submission_id, card_id, quantity, allocated_grading_cost
                ) VALUES (?, ?, ?, ?)
                """,
                (submission_id, card_id, quantity, item_cost),
            )
            db.execute(
                """
                UPDATE cards SET grading_status = ?, status = 'Grading',
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (values.get("status", "Preparing"), card_id),
            )
            _record_event(
                db, card_id, "Submitted for grading",
                f"{values['grader']} submission {values.get('submission_number', '')}".strip(),
                0, item_cost, "grading_submission", submission_id,
            )
        return submission_id


def all_grading_submissions() -> list[dict[str, Any]]:
    with connection() as db:
        rows = db.execute(
            """
            SELECT gs.*, COUNT(gi.id) AS card_rows, COALESCE(SUM(gi.quantity), 0) AS card_count
            FROM grading_submissions gs
            LEFT JOIN grading_items gi ON gi.submission_id = gs.id
            GROUP BY gs.id ORDER BY gs.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def grading_items(submission_id: int) -> list[dict[str, Any]]:
    with connection() as db:
        rows = db.execute(
            """
            SELECT gi.*, c.sku, c.card_name, c.set_name, c.cost, c.grading_cost,
                   c.graded_8_price, c.graded_9_price, c.psa_10_price
            FROM grading_items gi LEFT JOIN cards c ON c.id = gi.card_id
            WHERE gi.submission_id = ? ORDER BY gi.id
            """,
            (submission_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_grading_submission(
    submission_id: int,
    status: str,
    returned_date: str | None,
    results: list[dict[str, Any]],
) -> None:
    """Update submission progress and apply returned grades/costs to inventory."""
    with connection() as db:
        submission = db.execute(
            "SELECT * FROM grading_submissions WHERE id = ?", (submission_id,)
        ).fetchone()
        if not submission:
            raise ValueError("Grading submission was not found.")
        previous_status = submission["status"]
        completing_now = (
            status in {"Returned", "Rejected"}
            and previous_status not in {"Returned", "Rejected"}
        )
        db.execute(
            """
            UPDATE grading_submissions SET status = ?, returned_date = ?,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (status, returned_date or None, submission_id),
        )
        for result in results:
            item = db.execute(
                "SELECT * FROM grading_items WHERE id = ? AND submission_id = ?",
                (int(result["item_id"]), submission_id),
            ).fetchone()
            if not item or item["card_id"] is None:
                continue
            db.execute(
                "UPDATE grading_items SET grade = ?, certification_number = ? WHERE id = ?",
                (result.get("grade", ""), result.get("certification_number", ""), item["id"]),
            )
            card = db.execute("SELECT * FROM cards WHERE id = ?", (item["card_id"],)).fetchone()
            if not card:
                continue
            if completing_now:
                grade = str(result.get("grade", "")).strip()
                cost_increase = round(float(item["allocated_grading_cost"]) / int(item["quantity"]), 2)
                updates: dict[str, Any] = {
                    "grading_status": status,
                    "status": "Ready",
                    "grading_cost": round(float(card["grading_cost"] or 0) + cost_increase, 2),
                }
                if status == "Returned" and grade:
                    updates.update({
                        "condition": "Graded",
                        "grader": submission["grader"],
                        "grade": grade,
                        "certification_number": result.get("certification_number", ""),
                    })
                    numeric_grade = float(grade) if grade.replace(".", "", 1).isdigit() else 0
                    if numeric_grade >= 10 and card["psa_10_price"] is not None:
                        updates["market_price"] = card["psa_10_price"]
                    elif numeric_grade >= 9 and card["graded_9_price"] is not None:
                        updates["market_price"] = card["graded_9_price"]
                    elif numeric_grade >= 8 and card["graded_8_price"] is not None:
                        updates["market_price"] = card["graded_8_price"]
                assignments = ", ".join(f"{key} = ?" for key in updates)
                db.execute(
                    f"UPDATE cards SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (*updates.values(), item["card_id"]),
                )
                _record_event(
                    db, int(item["card_id"]),
                    "Grade received" if status == "Returned" else "Grading rejected",
                    f"{submission['grader']} {grade}; certification {result.get('certification_number', '')}".strip(),
                    0, float(item["allocated_grading_cost"]), "grading_submission", submission_id,
                )
            else:
                db.execute(
                    "UPDATE cards SET grading_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (status, item["card_id"]),
                )
                if status != previous_status:
                    _record_event(
                        db, int(item["card_id"]), "Grading status changed",
                        f"{previous_status} → {status}", 0, None,
                        "grading_submission", submission_id,
                    )


def inventory_events(card_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
    query = "SELECT * FROM inventory_events"
    parameters: list[Any] = []
    if card_id is not None:
        query += " WHERE card_id = ?"
        parameters.append(card_id)
    query += " ORDER BY id DESC LIMIT ?"
    parameters.append(limit)
    with connection() as db:
        rows = db.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]




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


def _allocated_quantities_from_rows(
    rows: list[sqlite3.Row], exclude_purchase_ids: list[int] | None = None,
) -> dict[int, int]:
    excluded = set(exclude_purchase_ids or [])
    excluded_groups = {
        row["allocation_group"] for row in rows
        if int(row["purchase_id"]) in excluded and row["allocation_group"]
    }
    grouped: dict[tuple[int, str], int] = {}
    for row in rows:
        if int(row["purchase_id"]) in excluded or row["allocation_group"] in excluded_groups:
            continue
        group = row["allocation_group"] or f"purchase:{row['purchase_id']}"
        key = (int(row["card_id"]), group)
        grouped[key] = max(grouped.get(key, 0), int(row["quantity"]))
    result: dict[int, int] = {}
    for (card_id, _), quantity in grouped.items():
        result[card_id] = result.get(card_id, 0) + quantity
    return result


def allocated_card_quantities(exclude_purchase_ids: list[int] | None = None) -> dict[int, int]:
    """Return physical quantities reserved by purchase allocations."""
    with connection() as db:
        rows = db.execute("SELECT * FROM purchase_allocations").fetchall()
    return _allocated_quantities_from_rows(rows, exclude_purchase_ids)


def allocate_purchase(purchase_id: int, allocations: list[dict[str, Any]]) -> None:
    allocate_purchases([purchase_id], allocations)


def allocate_purchases(purchase_ids: list[int], allocations: list[dict[str, Any]]) -> None:
    """Replace a purchase allocation and update inventory cost fields atomically."""
    purchase_ids = sorted({int(value) for value in purchase_ids})
    if not purchase_ids:
        raise ValueError("Choose at least one purchase.")
    if not allocations:
        raise ValueError("Choose at least one inventory card.")
    with connection() as db:
        requested_by_card: dict[int, int] = {}
        for allocation in allocations:
            card_id = int(allocation["card_id"])
            quantity = int(allocation["quantity"])
            if quantity <= 0:
                raise ValueError("Allocation quantity must be greater than zero.")
            requested_by_card[card_id] = requested_by_card.get(card_id, 0) + quantity
        for card_id, requested_quantity in requested_by_card.items():
            card = db.execute("SELECT quantity FROM cards WHERE id = ?", (card_id,)).fetchone()
            allocation_rows = db.execute("SELECT * FROM purchase_allocations").fetchall()
            allocated_elsewhere = _allocated_quantities_from_rows(
                allocation_rows, purchase_ids
            ).get(card_id, 0)
            if not card or requested_quantity + allocated_elsewhere > int(card["quantity"]):
                raise ValueError("An allocation exceeds the card's unallocated physical quantity.")
        marks = ", ".join("?" for _ in purchase_ids)
        previous = db.execute(
            f"SELECT card_id FROM purchase_allocations WHERE purchase_id IN ({marks})",
            tuple(purchase_ids),
        ).fetchall()
        affected_ids = {int(row["card_id"]) for row in previous}
        db.execute(
            f"DELETE FROM purchase_allocations WHERE purchase_id IN ({marks})", tuple(purchase_ids)
        )
        purchases = db.execute(
            f"SELECT id, total_cost FROM purchases WHERE id IN ({marks})", tuple(purchase_ids)
        ).fetchall()
        if len(purchases) != len(purchase_ids):
            raise ValueError("A selected purchase was not found.")
        group = "purchases:" + ",".join(str(value) for value in purchase_ids)
        total_units = sum(int(row["quantity"]) for row in allocations)
        for purchase in purchases:
            total_cents = int(round(float(purchase["total_cost"] or 0) * 100))
            base_cents, remainder = divmod(total_cents, total_units)
            for allocation in allocations:
                quantity = int(allocation["quantity"])
                card_id = int(allocation["card_id"])
                higher_units = min(quantity, remainder)
                remainder -= higher_units
                allocated_total = (base_cents * quantity + higher_units) / 100
                db.execute(
                    """
                    INSERT INTO purchase_allocations (
                        purchase_id, card_id, quantity, base_unit_cost,
                        higher_cost_units, allocated_total, allocation_group
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (purchase["id"], card_id, quantity, base_cents / 100,
                     higher_units, allocated_total, group),
                )
                affected_ids.add(card_id)
        for card_id in affected_ids:
            cost_total = db.execute(
                """
                SELECT COALESCE(SUM(allocated_total), 0)
                FROM purchase_allocations WHERE card_id = ?
                """,
                (card_id,),
            ).fetchone()[0]
            allocation_rows = db.execute("SELECT * FROM purchase_allocations").fetchall()
            allocated_quantity = _allocated_quantities_from_rows(allocation_rows).get(card_id, 0)
            allocated_total = float(cost_total)
            if allocated_quantity == 0:
                db.execute(
                    "UPDATE cards SET allocated_cost_total = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (card_id,),
                )
                continue
            average_cost = round(allocated_total / allocated_quantity, 2)
            db.execute(
                """
                UPDATE cards
                SET cost = ?, allocated_cost_total = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (average_cost, round(allocated_total, 2), card_id),
            )
        db.execute(
            f"UPDATE purchases SET status = 'Allocated' WHERE id IN ({marks})", tuple(purchase_ids)
        )


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
