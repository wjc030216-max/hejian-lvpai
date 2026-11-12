"""Server pricing tiers and the temporary flat-price override."""
import os
import unittest
from unittest.mock import patch

from phase2.pricing import price_for_actor_count, price_for_template, public_pricing


class PricingTests(unittest.TestCase):
    def test_standard_prices_follow_template_actor_count(self):
        with patch.dict(os.environ, {"AITRAVEL_AIVIDEO_PRICE_FEN": ""}):
            self.assertEqual([price_for_actor_count(i) for i in (1, 2, 3)],
                             [990, 1690, 1990])
            self.assertEqual(price_for_template({"actor_count": 2}), 1690)
            self.assertEqual(public_pricing()["pricing_mode"], "tier")
            self.assertEqual(public_pricing()["standard_price_tiers"],
                             {"1": 990, "2": 1690, "3": 1990})

    def test_flat_override_keeps_all_templates_at_one_dime(self):
        with patch.dict(os.environ, {"AITRAVEL_AIVIDEO_PRICE_FEN": "10"}):
            self.assertEqual([price_for_actor_count(i) for i in (1, 2, 3)],
                             [10, 10, 10])
            self.assertEqual(public_pricing()["price_tiers"],
                             {"1": 10, "2": 10, "3": 10})
            self.assertEqual(public_pricing()["standard_price_tiers"],
                             {"1": 990, "2": 1690, "3": 1990})

    def test_invalid_count_and_price_are_rejected(self):
        with patch.dict(os.environ, {"AITRAVEL_AIVIDEO_PRICE_FEN": ""}):
            with self.assertRaises(ValueError):
                price_for_actor_count(4)
        with patch.dict(os.environ, {"AITRAVEL_AIVIDEO_PRICE_FEN": "0"}):
            with self.assertRaises(ValueError):
                price_for_actor_count(1)


if __name__ == "__main__":
    unittest.main()
