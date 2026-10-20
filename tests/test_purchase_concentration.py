"""Synthetic concentration checks; separate from retailer evidence."""

from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from review_purchase_concentration import concentration, median


class PurchaseConcentrationTests(unittest.TestCase):
    def test_median_keeps_decimal_precision_and_handles_empty_and_even_groups(self):
        self.assertIsNone(median([]))
        self.assertEqual(median([Decimal("1.002"), Decimal("1.001")]), Decimal("1.0015"))
        self.assertEqual(median([Decimal("2"), Decimal("100"), Decimal("1")]), Decimal("2"))

    def test_partitions_ties_and_identification_populations_preserve_every_invoice(self):
        invoices = [
            {"period": "2010_jan_nov", "invoice_no": "123457", "gross": Decimal("100.01"), "has_customer_id": False, "recorded_date": "2010-01-02"},
            {"period": "2010_jan_nov", "invoice_no": "123456", "gross": Decimal("100.01"), "has_customer_id": True, "recorded_date": "2010-01-02"},
            {"period": "2010_jan_nov", "invoice_no": "123458", "gross": Decimal("1.005"), "has_customer_id": True, "recorded_date": "2010-02-02"},
        ]
        rows, examples = concentration(invoices)
        self.assertEqual(examples[0]["invoice_no"], "123456")
        for row in rows:
            self.assertEqual(Decimal(row["top_gross_gbp"]) + Decimal(row["remainder_gross_gbp"]), Decimal(row["gross_gbp"]))
            self.assertEqual(int(row["top_invoice_count"]) + int(row["remainder_invoice_count"]), int(row["invoice_count"]))
        get = lambda population: next(row for row in rows if row["period"] == "2010_jan_nov" and row["population"] == population and row["top_n"] == "1")
        self.assertEqual(get("all")["invoice_count"], "3")
        self.assertEqual(get("identified")["invoice_count"], "2")
        self.assertEqual(get("anonymous")["invoice_count"], "1")
        self.assertEqual(Decimal(get("identified")["gross_gbp"]) + Decimal(get("anonymous")["gross_gbp"]), Decimal(get("all")["gross_gbp"]))


if __name__ == "__main__":
    unittest.main()
