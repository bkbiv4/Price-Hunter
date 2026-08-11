import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import database
from business_imports import equal_card_allocations, read_ebay_workbook
from ebay import EbayConfig, listing_payloads
from grading import grading_opportunity
from receipt_scanner import allocate_receipt_amount, parse_receipt_text
from sales import packing_slip_text
from listings import build_description, build_title, suggested_price
from scp_import import collection_row_to_card, collection_type, import_identity
from sportscardspro import available_prices, build_card_search_query, inventory_grade_prices, matches_parallel, matches_terms, product_price_row, select_product_match
from sportscardspro import SportsCardsProClient
from unittest.mock import patch
from tcgcollector_import import pokemon_identity, reconcile_collection_rows, tcgcollector_row_to_card


class ListingRulesTests(unittest.TestCase):
    def test_sportscardspro_match_requires_exact_pokemon_identity(self):
        card = {
            "card_name": "Gloom [Reverse Holo] #2",
            "set_name": "Pokemon Obsidian Flames", "card_number": "2",
        }
        products = [
            {"id": 1, "product-name": "Gloom #2", "console-name": "Pokemon Obsidian Flames"},
            {"id": 2, "product-name": "Gloom [Reverse Holo] #2", "console-name": "Pokemon Obsidian Flames"},
            {"id": 3, "product-name": "Gloom [Reverse Holo] #2", "console-name": "Pokemon Paldea Evolved"},
        ]
        self.assertEqual(select_product_match(card, products)["id"], 2)

    def test_pokemon_search_uses_pricecharting_catalog(self):
        client = SportsCardsProClient("token")
        with patch.object(client, "_get", return_value={"products": [{"id": "1"}]}) as get:
            self.assertEqual(client.search_pokemon("Umbreon"), [{"id": "1"}])
        get.assert_called_once_with(
            "products", base_url=client.POKEMON_BASE_URL, q="Umbreon"
        )

    def test_tcgcollector_mapping_and_quantity_reconciliation(self):
        mapped = tcgcollector_row_to_card({
            "Card name": "Gloom", "Card number": "002/197", "Expansion": "Obsidian Flames",
            "Card variant": "Reverse Holo", "Card language": "English",
            "Card condition": "Mint", "Quantity": "3", "Price": "$0.89",
            "Rarity": "Uncommon", "Note": "",
        })
        existing = {
            "card_name": "Gloom [Reverse Holo] #2", "set_name": "Pokemon Obsidian Flames",
            "card_number": "2", "condition": "Raw / Ungraded", "quantity": 1,
        }
        self.assertEqual(mapped["card_name"], "Gloom [Reverse Holo] #2")
        self.assertEqual(mapped["market_price"], 0.89)
        self.assertEqual(pokemon_identity(mapped), pokemon_identity(existing))
        additions, stats = reconcile_collection_rows([mapped], [existing])
        self.assertEqual(additions[0]["quantity"], 2)
        self.assertEqual((stats["covered_units"], stats["new_units"]), (1, 2))

    def test_receipt_tax_allocation_reconciles_to_the_cent(self):
        allocations = allocate_receipt_amount(75.0, [220.0, 600.0, 430.0])
        self.assertEqual(allocations, [13.2, 36.0, 25.8])
        self.assertEqual(sum(allocations), 75.0)

    def test_ebay_listing_sales_csv_maps_aggregated_values(self):
        from io import BytesIO

        source = BytesIO("""Report for Jul 6, 2026 to Aug 5, 2026
Listing title,eBay item ID,Quantity sold,Total sales (Includes taxes),Item sales,Taxes and government fees paid by buyer to you,Taxes and government fees paid by buyer to eBay,Shipping and handling paid by buyer to you,Total selling costs,Insertion fees,Optional listing upgrade fees,Final value fees,Promoted Listings - General fees,Promoted Listings - Priority fees,Ads Express fees,Promoted Offsite - Fees,Other eBay fees,Deposit processing fees,Fee credits,Shipping labels cost (Amount you paid to buy shipping labels on eBay),Net sales (Net of taxes and selling costs),Average Selling price,Quantity sold via promoted listing
Test card,318612614447,1,"$1,433.66","$1,325.00",$0.00,$102.69,$5.97,$220.94,$0.00,$0.00,$190.36,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$0.00,$30.58,"$1,110.03","$1,433.66",0
""".encode())
        source.name = "listing-sales.csv"
        rows, errors = read_ebay_workbook(source)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sale_date"], "2026-08-05")
        self.assertEqual(rows[0]["item_subtotal"], 1325.0)
        self.assertEqual(rows[0]["shipping_charged"], 5.97)
        self.assertEqual(rows[0]["fees"], 190.36)
        self.assertEqual(rows[0]["shipping_label_cost"], 30.58)
        self.assertEqual(rows[0]["net_amount"], 1110.03)

    def test_ebay_all_orders_csv_maps_order_details(self):
        from io import BytesIO

        source = BytesIO("""Sales Record Number,Order Number,Buyer Username,Buyer Name,Item Number,Item Title,Custom Label,Quantity,Sold For,Shipping And Handling,Seller Collected Tax,Sale Date
112,24-14934-88735,gamecosmos,Anthony Agbuis,318508625049,Test card,PH-000001,1,$900.00,$5.97,$0.00,Jul-25-26
""".encode())
        source.name = "all-orders.csv"
        rows, errors = read_ebay_workbook(source)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sale_date"], "2026-07-25")
        self.assertEqual(rows[0]["order_number"], "24-14934-88735")
        self.assertEqual(rows[0]["sku"], "PH-000001")
        self.assertEqual(rows[0]["buyer"], "Anthony Agbuis")
        self.assertEqual((rows[0]["item_subtotal"], rows[0]["shipping_charged"]), (900.0, 5.97))
        self.assertEqual(rows[0]["net_amount"], 905.97)

    def test_amazon_receipt_scanner_extracts_expense_fields(self):
        receipt = parse_receipt_text("""
            Order Placed: July 29, 2026
            Amazon.com order number: 112-2188920-3015457
            1 of: Graded Card Case, Trading Card Storage Box for Card Slab $40.84
            Sales Tax: $2.45
            Grand Total: USD 43.29
            Payment Method:
            MasterCard | Last digits: 8861
        """, "order-document.pdf")
        self.assertEqual(receipt["expense_date"], "2026-07-29")
        self.assertEqual(receipt["vendor"], "Amazon.com")
        self.assertEqual(receipt["category"], "Supplies")
        self.assertEqual((receipt["amount"], receipt["tax"], receipt["total"]), (40.84, 2.45, 43.29))
        self.assertEqual(receipt["receipt_ref"], "112-2188920-3015457")
        self.assertEqual(receipt["payment_account"], "MasterCard ending in 8861")

    def test_store_receipt_ocr_text_extracts_expense_fields(self):
        receipt = parse_receipt_text("""
            The Fantastic Store
            August 5, 2026
            Receipt: 3rS1
            Magic The Gathering Play Booster Box Spiderman x 2 $220.00
            One Piece Booster Box Heroines x 2 $600.00
            One Piece Booster Box Time Of Battle x 2 $430.00
            Subtotal $1,250.00
            Virginia State Sales Tax (6%) $75.00
            Total $1,325.00
            Mastercard 8067
        """, "IMG_2484.HEIC")
        self.assertEqual(receipt["expense_date"], "2026-08-05")
        self.assertEqual(receipt["vendor"], "The Fantastic Store")
        self.assertEqual(receipt["category"], "Inventory")
        self.assertEqual((receipt["amount"], receipt["tax"], receipt["total"]), (1250.0, 75.0, 1325.0))
        self.assertEqual(receipt["receipt_ref"], "3rS1")
        self.assertEqual(receipt["payment_account"], "Mastercard ending in 8067")
        self.assertEqual(len(receipt["line_items"]), 3)
        self.assertEqual(receipt["line_items"][0]["quantity"], 2)
        self.assertEqual(receipt["line_items"][0]["total"], 220.0)

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

    def test_collection_type_is_derived_from_set_name(self):
        self.assertEqual(collection_type("Football Cards 2024 Panini Mosaic"), "Football")
        self.assertEqual(collection_type("One Piece Premium Booster 2"), "One Piece")
        self.assertEqual(collection_type("Pokemon Scarlet & Violet 151"), "Pokemon")

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
            "payment_method": "Cash",
            "cash_received": 50.0,
            "trade_value": 0.0,
        })
        card = database.get_card(self.card_id)
        sale = next(row for row in database.all_sales() if row["id"] == sale_id)
        self.assertEqual(card["quantity"], 1)
        self.assertEqual((sale["net_amount"], sale["cost_of_goods"], sale["profit"]), (42.0, 10.0, 32.0))
        self.assertEqual((sale["payment_method"], sale["cash_received"]), ("Cash", 50.0))
        self.assertEqual(database.inventory_events(self.card_id)[0]["event_type"], "Sold")

    def test_trade_value_sale_marks_depleted_inventory_sold(self):
        trade_card_id = database.add_card({
            "card_name": "Trade Card", "set_name": "Test Set", "quantity": 1,
            "cost": 25.0, "market_price": 60.0,
        })
        sale_id = database.create_manual_sale(trade_card_id, {
            "sale_date": "2026-08-05", "marketplace": "Direct",
            "title": "Trade Card", "quantity": 1, "item_subtotal": 75.0,
            "payment_method": "Trade-in value", "cash_received": 0.0,
            "trade_value": 75.0, "status": "Completed",
        })
        card = database.get_card(trade_card_id)
        sale = next(row for row in database.all_sales() if row["id"] == sale_id)
        self.assertEqual((card["quantity"], card["status"]), (0, "Sold"))
        self.assertEqual((sale["trade_value"], sale["net_amount"], sale["profit"]), (75.0, 75.0, 50.0))

        database.update_sale(sale_id, {
            "title": "Edited trade sale", "item_subtotal": 80.0,
            "cash_received": 0.0, "trade_value": 80.0,
            "fees": 5.0, "shipping_charged": 0.0,
            "promoted_listing_fees": 0.0, "shipping_label_cost": 0.0,
        })
        edited_sale = next(row for row in database.all_sales() if row["id"] == sale_id)
        self.assertEqual(edited_sale["title"], "Edited trade sale")
        self.assertEqual((edited_sale["net_amount"], edited_sale["profit"]), (75.0, 50.0))

        database.delete_sale(sale_id, restore_inventory=True)
        restored_card = database.get_card(trade_card_id)
        self.assertEqual((restored_card["quantity"], restored_card["status"]), (1, "Ready"))
        self.assertFalse(any(row["id"] == sale_id for row in database.all_sales()))

    def test_manual_purchase_and_expense_are_saved(self):
        purchase_id = database.add_purchase({
            "purchase_date": "2026-08-04", "description": "Football lot",
            "purchase_price": 100.0, "shipping": 8.0, "tax": 7.0,
            "total_cost": 115.0,
        })
        expense_id = database.add_expense({
            "expense_date": "2026-08-04", "description": "Top loaders",
            "quantity": 2, "amount": 10.0, "tax": 1.0, "shipping": 4.0,
            "total": 25.0,
        })
        self.assertEqual(database.all_purchases()[0]["id"], purchase_id)
        self.assertEqual(database.all_purchases()[0]["total_cost"], 115.0)
        self.assertEqual(database.all_expenses()[0]["id"], expense_id)
        self.assertEqual(database.all_expenses()[0]["total"], 25.0)

        database.update_purchase(purchase_id, {
            "description": "Updated football lot", "cards_expected": 120,
            "purchase_price": 110.0, "shipping": 9.0, "tax": 8.0,
            "total_cost": 127.0,
        })
        updated_purchase = database.all_purchases()[0]
        self.assertEqual(updated_purchase["description"], "Updated football lot")
        self.assertEqual(updated_purchase["cards_expected"], 120)
        self.assertEqual(updated_purchase["total_cost"], 127.0)

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
