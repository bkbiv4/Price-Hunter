import unittest

from listings import build_description, build_title, suggested_price
from scp_import import collection_row_to_card, import_identity
from sportscardspro import available_prices, build_card_search_query, inventory_grade_prices, matches_terms, product_price_row


class ListingRulesTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
