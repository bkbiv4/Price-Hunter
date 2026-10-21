import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import database


class SalesManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.override = patch.object(database, "DB_PATH", Path(self.temp.name) / "test.db")
        self.override.start()
        database.initialize()
        self.card = database.add_card({"card_name": "First card", "quantity": 3, "cost": 10})
        self.other = database.add_card({"card_name": "Second card", "quantity": 1, "cost": 25})
        database.import_sales([{"source_key": "test-order", "title": "Imported sale", "quantity": 1,
                                "item_subtotal": 100, "net_amount": 90, "gross_net_amount": 90, "fees": 10}])
        self.sale = database.all_sales()[0]["id"]

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def test_repeated_link_and_quantity_edit_apply_only_the_difference(self):
        database.link_sale_to_card(self.sale, self.card)
        database.link_sale_to_card(self.sale, self.card)
        self.assertEqual(database.get_card(self.card)["quantity"], 2)
        database.set_sale_items(self.sale, [{"card_id": self.card, "quantity": 2}])
        self.assertEqual(database.get_card(self.card)["quantity"], 1)
        database.set_sale_items(self.sale, [{"card_id": self.card, "quantity": 1}])
        self.assertEqual(database.get_card(self.card)["quantity"], 2)
        self.assertEqual(database.all_sales()[0]["cost_of_goods"], 10)

    def test_replacement_restores_old_card_and_deducts_new_card(self):
        database.link_sale_to_card(self.sale, self.card)
        database.link_sale_to_card(self.sale, self.other)
        self.assertEqual(database.get_card(self.card)["quantity"], 3)
        self.assertEqual(database.get_card(self.other)["quantity"], 0)
        self.assertEqual(database.get_card(self.other)["status"], "Sold")
        self.assertEqual([r["card_id"] for r in database.sale_items(self.sale)], [self.other])
        self.assertEqual(database.all_sales()[0]["profit"], 65)
        database.set_sale_items(self.sale, [])
        self.assertEqual(database.get_card(self.other)["quantity"], 1)
        self.assertIsNone(database.all_sales()[0]["profit"])

    def test_insufficient_stock_rolls_back_entire_replacement(self):
        database.link_sale_to_card(self.sale, self.card)
        with self.assertRaisesRegex(ValueError, "insufficient"):
            database.set_sale_items(self.sale, [{"card_id": self.other, "quantity": 2}])
        self.assertEqual(database.get_card(self.card)["quantity"], 2)
        self.assertEqual(database.get_card(self.other)["quantity"], 1)
        self.assertEqual(database.sale_items(self.sale)[0]["card_id"], self.card)

    def test_historical_link_can_be_deducted_once_later(self):
        database.link_sale_to_card(self.sale, self.card, reduce_inventory=False)
        self.assertEqual(database.get_card(self.card)["quantity"], 3)
        database.link_sale_to_card(self.sale, self.card)
        database.link_sale_to_card(self.sale, self.card)
        self.assertEqual(database.get_card(self.card)["quantity"], 2)

    def test_removing_undeducted_link_does_not_invent_stock(self):
        database.link_sale_to_card(self.sale, self.card, reduce_inventory=False)
        database.set_sale_items(self.sale, [])
        self.assertEqual(database.get_card(self.card)["quantity"], 3)

    def test_manual_sale_already_deducted_is_not_deducted_again(self):
        sale = database.create_manual_sale(self.card, {"title": "Manual", "quantity": 1, "item_subtotal": 50})
        database.link_sale_to_card(sale, self.card)
        self.assertEqual(database.get_card(self.card)["quantity"], 2)

    def test_returns_lock_inventory_links(self):
        database.link_sale_to_card(self.sale, self.card)
        database.update_sale_adjustment(self.sale, "Returned", 90, 0, 0, "Return", True)
        with self.assertRaisesRegex(ValueError, "Returned"):
            database.link_sale_to_card(self.sale, self.card)
        self.assertEqual(database.get_card(self.card)["quantity"], 3)

    def test_financial_edit_preserves_stock_and_refunds_and_updates_line_prices(self):
        database.set_sale_items(self.sale, [{"card_id": self.card, "quantity": 2}, {"card_id": self.other, "quantity": 1}])
        database.update_sale_adjustment(self.sale, "Partially Refunded", 5, 2, 1, "Adjustment")
        database.update_sale(self.sale, {"title": "Edited", "item_subtotal": 120, "fees": 12})
        sale = database.all_sales()[0]
        self.assertEqual((sale["net_amount"], sale["cost_of_goods"], sale["profit"]), (100, 45, 55))
        self.assertEqual(database.get_card(self.card)["quantity"], 1)
        self.assertEqual(database.get_card(self.other)["quantity"], 0)
        self.assertAlmostEqual(sum(r["unit_price"]*r["quantity"] for r in database.sale_items(self.sale)), 120)

    def test_multicard_sale_is_not_automatically_replaced_with_one_match(self):
        database.set_sale_items(self.sale, [{"card_id": self.card, "quantity": 1}, {"card_id": self.other, "quantity": 1}])
        with database.connection() as db:
            db.execute("UPDATE sales SET sku=? WHERE id=?", (database.get_card(self.card)["sku"], self.sale))
        self.assertEqual(database.auto_match_ebay_sales()["matched"], 0)
        self.assertEqual(len(database.sale_items(self.sale)), 2)


if __name__ == "__main__":
    unittest.main()
