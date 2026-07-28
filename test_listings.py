import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import database
from business_imports import equal_card_allocations
from ebay import EbayConfig, listing_payloads
from grading import grading_opportunity
from sales import packing_slip_text
from listings import build_description, build_title, suggested_price
from scp_import import collection_row_to_card, import_identity
from sportscardspro import available_prices, build_card_search_query, inventory_grade_prices, matches_parallel, matches_terms, product_price_row


class ListingRulesTests(unittest.TestCase):
    def test_grading_opportunity_calculates_break_even_and_expected_profit(self):
        result = grading_opportunity(
            {
                "id": 1,
                "sku": "PH-1",
                "card_name": "Test",
                "quantity": 1,
                "cost": 10,
                "market_price": 20,
                "graded_8_price": 30,
                "graded_9_price": 60,
                "psa_10_price": 120,
            },
            grading_cost=20,
            selling_cost_rate=0.10,
            grade_probabilities={"8 / 8.5": 0.2, "9": 0.5, "10": 0.3},
        )
        self.assertEqual(result["break_even_grade"], "9")
        self.assertEqual(result["expected_value"], 72.0)
        self.assertEqual(result["expected_profit"], 34.8)

    def test_title_is_limited_to_80_characters(self):
        title = build_title("A" * 70, "B" * 30, "PSA 10")
        self.assertLessEqual(len(title), 80)

    def test_market_price_wins_when_higher(self):
        self.assertEqual(suggested_price(25, 10, 30), 25)

    def test_markup_price_wins_when_higher(self):
        self.assertEqual(suggested_price(10, 20, 25), 25)

    def test_grader_and_grade_are_in_title(self):
        title = build_title("Michael Jordan", "1986 Fleer", "10", "57", "PSA")
        self.assertIn("PSA 10", title)

    def test_certification_number_is_in_description(self):
        description = build_description("Card", "Set", "Graded", "10", "", "PH-1", "PSA", "12345")
        self.assertIn("Certification number: 12345", description)

    def test_price_fields_are_converted_from_cents(self):
        prices = available_prices({"loose-price": 1234, "manual-only-price": 9999, "bgs-10-price": 0})
        self.assertEqual(prices, {"Raw / Ungraded": 12.34, "PSA 10": 99.99})

    def test_set_filter_matches_words_in_any_order(self):
        self.assertTrue(matches_terms("Football Cards 2024 Panini Mosaic", "2024 Mosaic Panini"))
        self.assertFalse(matches_terms("Football Cards 2023 Panini Mosaic", "2024 Mosaic"))

    def test_product_price_row_is_table_ready(self):
        row = product_price_row({
            "id": 42,
            "product-name": "Player #1 [Black]",
            "console-name": "Football Cards 2024 Panini Mosaic",
            "loose-price": 1234,
        })
        self.assertEqual(row["Product ID"], "42")
        self.assertEqual(row["Raw / Ungraded"], 12.34)

    def test_structured_search_omits_blank_parts(self):
        query = build_card_search_query("2024", "Panini", "", "Patrick Mahomes", "#MM4", "White")
        self.assertEqual(query, "2024 Panini Patrick Mahomes #MM4 White")

    def test_sportscardspro_collection_row_maps_prices_and_grade(self):
        card = collection_row_to_card({
            "id": "123",
            "product-name": "Patrick Mahomes II [White] #MM4",
            "console-name": "Football Cards 2024 Panini Mosaic",
            "price-in-pennies": "12345",
            "include-string": "PSA 10",
            "cost-basis-in-pennies": "5000",
            "quantity": "2",
            "grading-company": "PSA",
            "grading-cert-id": "999",
            "condition-string": "Normal wear",
        })
        self.assertEqual(card["market_price"], 123.45)
        self.assertEqual(card["cost"], 50.0)
        self.assertEqual(card["quantity"], 2)
        self.assertEqual((card["condition"], card["grader"], card["grade"]), ("Graded", "PSA", "10"))
        self.assertEqual(card["card_number"], "MM4")
        self.assertEqual(import_identity(card), ("123", "Graded", "PSA", "10", "999"))
        self.assertEqual(card["psa_10_price"], 123.45)

    def test_inventory_grade_prices_use_documented_fields(self):
        prices = inventory_grade_prices({
            "new-price": 800,
            "graded-price": 900,
            "manual-only-price": 1000,
        })
        self.assertEqual(prices, {
            "graded_8_price": 8.0,
            "graded_9_price": 9.0,
            "psa_10_price": 10.0,
        })

    def test_parallel_filter_only_uses_variant_labels(self):
        self.assertFalse(matches_parallel("Cam Reddish [Green Laser] #53", "Red"))
        self.assertTrue(matches_parallel("Walker Kessler [Red Mojo] #250", "Red"))
        self.assertTrue(matches_parallel("Jaime Jaquez Jr. [Choice Red] #213", "Red"))
        self.assertTrue(matches_parallel("Player [Alternate Art] #1", "Alternate Arts"))


class InventoryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        database.DB_PATH = Path(self.temp_directory.name) / "test.db"
        database.initialize()
        self.card_id = database.add_card({
            "card_name": "Test Player",
            "set_name": "Test Set",
            "quantity": 2,
            "cost": 10.0,
            "market_price": 20.0,
            "graded_8_price": 30.0,
            "graded_9_price": 50.0,
            "psa_10_price": 100.0,
        })

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.temp_directory.cleanup()

    def test_manual_sale_reduces_quantity_and_records_profit_and_history(self):
        sale_id = database.create_manual_sale(self.card_id, {
            "sale_date": "2026-07-27",
            "marketplace": "eBay",
            "title": "Test Player",
            "quantity": 1,
            "item_subtotal": 50.0,
            "shipping_charged": 5.0,
            "fees": 7.0,
            "promoted_listing_fees": 2.0,
            "shipping_label_cost": 4.0,
        })
        card = database.get_card(self.card_id)
        sale = next(row for row in database.all_sales() if row["id"] == sale_id)
        self.assertEqual(card["quantity"], 1)
        self.assertEqual((sale["net_amount"], sale["cost_of_goods"], sale["profit"]), (42.0, 10.0, 32.0))
        self.assertEqual(database.inventory_events(self.card_id)[0]["event_type"], "Sold")

    def test_returned_grade_updates_cost_market_value_and_history(self):
        submission_id = database.create_grading_submission(
            {
                "submission_number": "PSA-1",
                "grader": "PSA",
                "status": "Submitted",
                "grading_fee": 20.0,
                "shipping_cost": 4.0,
                "insurance_cost": 0.0,
                "other_cost": 0.0,
            },
            [{"card_id": self.card_id, "quantity": 1}],
        )
        item = database.grading_items(submission_id)[0]
        database.update_grading_submission(
            submission_id,
            "Returned",
            "2026-07-27",
            [{"item_id": item["id"], "grade": "10", "certification_number": "123"}],
        )
        card = database.get_card(self.card_id)
        self.assertEqual((card["condition"], card["grader"], card["grade"]), ("Graded", "PSA", "10"))
        self.assertEqual((card["cost"], card["market_price"]), (34.0, 100.0))
        self.assertEqual(database.inventory_events(self.card_id)[0]["event_type"], "Grade received")
        database.update_grading_submission(
            submission_id,
            "Returned",
            "2026-07-27",
            [{"item_id": item["id"], "grade": "10", "certification_number": "123"}],
        )
        self.assertEqual(database.get_card(self.card_id)["cost"], 34.0)

    def test_lot_sale_and_return_restore_inventory_only_once(self):
        second_card_id = database.add_card({
            "card_name": "Second Card",
            "set_name": "Test Set",
            "quantity": 1,
            "cost": 5.0,
        })
        sale_id = database.create_multi_card_sale(
            {
                "sale_date": "2026-07-27",
                "marketplace": "Direct",
                "shipping_charged": 5.0,
                "fees": 3.0,
                "promoted_listing_fees": 0.0,
                "shipping_label_cost": 4.0,
                "status": "Completed",
            },
            [
                {"card_id": self.card_id, "quantity": 1, "unit_price": 30.0},
                {"card_id": second_card_id, "quantity": 1, "unit_price": 20.0},
            ],
        )
        self.assertEqual(database.get_card(self.card_id)["quantity"], 1)
        self.assertEqual(database.get_card(second_card_id)["quantity"], 0)
        database.update_sale_adjustment(
            sale_id, "Returned", 55.0, 0.0, 6.0, "Returned lot", True
        )
        self.assertEqual(database.get_card(self.card_id)["quantity"], 2)
        self.assertEqual(database.get_card(second_card_id)["quantity"], 1)
        database.update_sale_adjustment(
            sale_id, "Returned", 55.0, 0.0, 6.0, "Returned lot", True
        )
        self.assertEqual(database.get_card(self.card_id)["quantity"], 2)
        sale = next(row for row in database.all_sales() if row["id"] == sale_id)
        self.assertEqual(sale["cost_of_goods"], 0.0)
        self.assertTrue(sale["inventory_restored"])

    def test_packing_slip_lists_every_sale_item(self):
        text = packing_slip_text(
            {"id": 1, "sale_date": "2026-07-27", "marketplace": "Direct"},
            [
                {"quantity": 1, "card_name": "First", "card_sku": "PH-1"},
                {"quantity": 2, "card_name": "Second", "card_sku": "PH-2"},
            ],
        )
        self.assertIn("1 x First [PH-1]", text)
        self.assertIn("2 x Second [PH-2]", text)

    def test_ebay_sale_automatically_matches_unique_inventory_sku(self):
        card = database.get_card(self.card_id)
        database.import_sales([{
            "source_key": "ebay-order-1",
            "sale_date": "2026-07-27",
            "marketplace": "eBay",
            "order_number": "ORDER-1",
            "item_id": "",
            "sku": card["sku"],
            "title": "Test Player",
            "quantity": 1,
            "item_subtotal": 50.0,
            "shipping_charged": 0.0,
            "fees": 5.0,
            "shipping_label_cost": 4.0,
            "net_amount": 41.0,
        }])
        result = database.auto_match_ebay_sales()
        sale = database.all_sales()[0]
        self.assertEqual(result["matched"], 1)
        self.assertEqual(sale["card_id"], self.card_id)
        self.assertEqual((sale["cost_of_goods"], sale["profit"]), (10.0, 31.0))
        self.assertEqual(database.get_card(self.card_id)["quantity"], 2)
        self.assertEqual(database.auto_match_ebay_sales()["matched"], 0)

    def test_equal_card_allocation_reconciles_to_the_cent(self):
        allocations = equal_card_allocations(
            10.00,
            [{"id": 1, "quantity": 1}, {"id": 2, "quantity": 2}],
        )
        self.assertEqual([row["allocated_total"] for row in allocations], [3.34, 6.66])
        self.assertEqual(sum(row["allocated_total"] for row in allocations), 10.00)
        self.assertEqual(allocations[0]["higher_cost_units"], 1)

    def test_equal_card_allocation_requires_physical_cards(self):
        with self.assertRaises(ValueError):
            equal_card_allocations(10, [])

    def test_ebay_payload_uses_saved_listing_and_policy_settings(self):
        config = EbayConfig(
            environment="Sandbox",
            client_id="",
            client_secret="",
            refresh_token="",
            access_token="token",
            marketplace_id="EBAY_US",
            merchant_location_key="home",
            category_id="123",
            fulfillment_policy_id="f",
            payment_policy_id="p",
            return_policy_id="r",
            condition="USED_EXCELLENT",
        )
        inventory, offer = listing_payloads({
            "sku": "PH-1",
            "quantity": 1,
            "listing_title": "Card title",
            "listing_description": "Card description",
            "list_price": 12.34,
            "image_urls": "https://example.com/front.jpg\nhttps://example.com/back.jpg",
            "set_name": "Set",
            "card_number": "1",
        }, config)
        self.assertEqual(inventory["product"]["imageUrls"], [
            "https://example.com/front.jpg",
            "https://example.com/back.jpg",
        ])
        self.assertEqual(offer["pricingSummary"]["price"]["value"], "12.34")
        self.assertEqual(offer["listingPolicies"]["fulfillmentPolicyId"], "f")


if __name__ == "__main__":
    unittest.main()
