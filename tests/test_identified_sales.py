"""Synthetic full-period population and exact identity cases; not business evidence."""

import csv
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cleaning_rules import load_code_rules
from compare_identified_sales import compare
from identified_sales_breakdown import aggregate, COLUMNS, exact_identity
from monthly_sales_baseline import aggregate as monthly_aggregate, COLUMNS as MONTHLY_COLUMNS, ratio
from test_monthly_sales import source_row


class IdentifiedSalesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_code_rules()

    def source(self):
        return [source_row(), source_row(invoice="123457", timestamp="2010-02-10 10:00:00"),
                source_row(invoice="123458", customer="__NULL__"),
                source_row(invoice="123459", timestamp="2011-01-10 10:00:00", sheet="Year 2010-2011")]

    def test_full_period_distinct_customer_is_not_sum_of_months(self):
        rows, _ = aggregate(self.source(), self.rules, "__NULL__")
        self.assertEqual(rows[0]["identified_purchasing_customers"], "1")
        self.assertEqual(rows[0]["identified_purchase_invoice_count"], "2")
        self.assertEqual(Decimal(rows[0]["purchases_per_identified_customer"]), Decimal(2))
        self.assertEqual(rows[0]["anonymous_purchase_invoice_count"], "1")

    def test_repeated_lines_retained_credits_and_december_excluded(self):
        source = self.source() + [source_row(), source_row(invoice="C123456", quantity="-1"),
                                  source_row(invoice="123460", code="POST", price="100"),
                                  source_row(invoice="123461", timestamp="2010-12-01 00:00:00"),
                                  source_row(invoice="123462", timestamp="2011-12-01 00:00:00", sheet="Year 2010-2011")]
        rows, _ = aggregate(source, self.rules, "__NULL__")
        self.assertEqual(Decimal(rows[0]["identified_gross_merchandise_sales_gbp"]), Decimal("3.375"))
        self.assertEqual(rows[0]["identified_purchase_invoice_count"], "2")

    def test_exact_fraction_identity_does_not_use_rounded_frequency(self):
        row = {"identified_purchasing_customers": "3", "identified_purchase_invoice_count": "4",
               "identified_gross_merchandise_sales_gbp": "1.01"}
        check = exact_identity(row)
        self.assertTrue(check["identity_matches"])
        self.assertEqual(check["exact_residual_fraction_gbp"], "0")
        self.assertEqual(Fraction(check["exact_product_fraction_gbp"]), Fraction(101, 100))
        displayed_product = 3 * ratio(4, 3) * ratio(Decimal("1.01"), 4)
        self.assertNotEqual(displayed_product, Decimal("1.01"))

    def test_conflicting_invoice_customers_block_and_empty_population_is_undefined(self):
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            aggregate([source_row(), source_row(customer="__NULL__")], self.rules, "__NULL__")
        rows, evidence = aggregate([], self.rules, "__NULL__")
        self.assertEqual(rows[0]["purchases_per_identified_customer"], "")
        self.assertFalse(evidence["period_checks"][0]["exact_identity"]["defined"])

    def compare_fixture(self, mutate):
        rows, _ = aggregate(self.source(), self.rules, "__NULL__")
        monthly, _, _ = monthly_aggregate(self.source(), self.rules, "__NULL__")
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            expected, observed, baseline = [Path(folder) / name for name in ("expected.csv", "observed.csv", "baseline.csv")]
            def write(path, values, columns):
                with path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=columns)
                    writer.writeheader()
                    writer.writerows(values)
            write(expected, rows, COLUMNS)
            observed_rows, columns = mutate([row.copy() for row in rows])
            write(observed, observed_rows, columns)
            write(baseline, monthly, MONTHLY_COLUMNS)
            before = [path.read_bytes() for path in (expected, observed, baseline)]
            result = compare(expected, observed, baseline)
            self.assertEqual(before, [path.read_bytes() for path in (expected, observed, baseline)])
            return result

    def test_comparison_accepts_reordering_and_decimal_scale(self):
        def mutate(rows):
            rows[0]["identified_gross_merchandise_sales_gbp"] = "2.250000000"
            return list(reversed(rows)), list(reversed(COLUMNS))
        result = self.compare_fixture(mutate)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["numeric_comparisons_total"], 20)
        self.assertTrue(all(item["identity_matches"] for item in result["exact_identity_checks"]))

    def test_comparison_detects_money_count_and_ratio_mismatches(self):
        def mutate(rows):
            rows[0]["identified_purchasing_customers"] = "2"
            rows[0]["purchases_per_identified_customer"] = "1.999999999"
            rows[0]["anonymous_gross_merchandise_sales_gbp"] = "1.125000001"
            return rows, COLUMNS
        result = self.compare_fixture(mutate)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["field_mismatches"]), 3)
        self.assertTrue(result["control_errors"])

    def test_comparison_rejects_duplicate_period_and_missing_column(self):
        def duplicate(rows):
            rows[1] = rows[0].copy()
            return rows, COLUMNS
        self.assertEqual(self.compare_fixture(duplicate)["status"], "failed")
        def schema(rows):
            return [{key: value for key, value in row.items() if key != COLUMNS[-1]} for row in rows], COLUMNS[:-1]
        self.assertEqual(self.compare_fixture(schema)["status"], "failed")

    def test_comparison_rejects_nonfinite_values(self):
        def mutate(rows):
            rows[0]["identified_gross_merchandise_sales_gbp"] = "NaN"
            return rows, COLUMNS
        result = self.compare_fixture(mutate)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["field_mismatches"])


if __name__ == "__main__":
    unittest.main()
