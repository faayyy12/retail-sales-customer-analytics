"""Synthetic monthly edge cases; separate from retailer and cloud evidence."""

import csv
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cleaning_rules import load_code_rules
from compare_monthly_sales import compare
from monthly_sales_baseline import aggregate, COLUMNS, MONTHS, ratio


def source_row(invoice="123456", timestamp="2010-01-10 10:00:00", customer="12345", quantity="1", price="1.125", sheet="Year 2009-2010", code="12345"):
    return {"invoice_no": invoice, "invoice_datetime": timestamp, "customer_id": customer,
            "quantity": quantity, "unit_price": price, "source_sheet": sheet,
            "stock_code_raw": code, "description_raw": "Example product",
            "description_source_type": "text", "country": "UK"}


class MonthlySalesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_code_rules()

    def aggregate(self, rows):
        return aggregate(rows, self.rules, "__NULL__")[0]

    def test_repeated_lines_preserved_and_anonymous_invoices_not_customers(self):
        rows = [source_row(), source_row(), source_row(invoice="123457", customer="__NULL__"),
                source_row(invoice="123458", customer="__NULL__"), source_row(invoice="123459")]
        month = self.aggregate(rows)[0]
        self.assertEqual(Decimal(month["gross_merchandise_sales_gbp"]), Decimal("5.625"))
        self.assertEqual(month["purchase_invoice_count"], "4")
        self.assertEqual(month["identified_purchasing_customers"], "1")
        self.assertEqual(month["identified_purchase_invoice_count"], "2")
        self.assertEqual(month["anonymous_purchase_invoice_count"], "2")

    def test_credits_use_recorded_month_and_nonmerchandise_excluded(self):
        rows = [source_row(), source_row(invoice="C123456", timestamp="2010-02-01 00:00:00", quantity="-1"),
                source_row(invoice="123457", code="POST", price="100")]
        months = self.aggregate(rows)
        self.assertEqual(Decimal(months[0]["gross_merchandise_sales_gbp"]), Decimal("1.125"))
        self.assertEqual(months[0]["recorded_merchandise_credits_gbp"], "0")
        self.assertEqual(Decimal(months[1]["net_recorded_merchandise_sales_gbp"]), Decimal("-1.125"))
        self.assertEqual(months[1]["average_purchase_invoice_value_gbp"], "")

    def test_cutoffs_overlap_and_calendar(self):
        rows = [source_row(invoice="123450", timestamp="2009-12-31 23:59:59"),
                source_row(invoice="123451", timestamp="2010-01-01 00:00:00"),
                source_row(invoice="123452", timestamp="2010-11-30 23:59:59"),
                source_row(invoice="123453", timestamp="2010-12-01 00:00:00"),
                source_row(invoice="123454", timestamp="2010-12-01 00:00:00", sheet="Year 2010-2011"),
                source_row(invoice="123455", timestamp="2011-01-01 00:00:00", sheet="Year 2010-2011"),
                source_row(invoice="123456", timestamp="2011-11-30 23:59:59", sheet="Year 2010-2011"),
                source_row(invoice="123457", timestamp="2011-12-01 00:00:00", sheet="Year 2010-2011")]
        months = self.aggregate(rows)
        self.assertEqual([row["month_start"] for row in months], MONTHS)
        self.assertEqual(sum(int(row["purchase_invoice_count"]) for row in months), 4)

    def test_conflicting_invoice_days_or_customer_presence_block(self):
        for changed in (source_row(timestamp="2010-01-11 10:00:00"), source_row(customer="__NULL__")):
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                self.aggregate([source_row(), changed])

    def test_ratios_have_explicit_precision_and_null_zero_denominator(self):
        self.assertEqual(ratio(1, 3), Decimal("0.333333333"))
        self.assertEqual(ratio(Decimal("0.0000000005"), 1), Decimal("0.000000001"))
        self.assertEqual(ratio(Decimal("-0.0000000005"), 1), Decimal("-0.000000001"))
        self.assertIsNone(ratio(0, 0))

    def compare_rows(self, mutate):
        rows = self.aggregate([source_row()])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            expected, observed = Path(folder) / "expected.csv", Path(folder) / "observed.csv"
            def write(path, values, columns):
                with path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=columns)
                    writer.writeheader()
                    writer.writerows(values)
            write(expected, rows, COLUMNS)
            observed_rows, columns = mutate([row.copy() for row in rows])
            write(observed, observed_rows, columns)
            before = (expected.read_bytes(), observed.read_bytes())
            result = compare(expected, observed)
            self.assertEqual(before, (expected.read_bytes(), observed.read_bytes()))
            return result

    def test_comparison_reordered_decimal_scales_and_blank_ratios(self):
        def mutate(rows):
            rows[0]["gross_merchandise_sales_gbp"] = "1.125000000"
            return list(reversed(rows)), list(reversed(COLUMNS))
        result = self.compare_rows(mutate)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["numeric_comparisons_total"], 220)

    def test_comparison_detects_exact_money_ratio_and_count_mismatches(self):
        def mutate(rows):
            rows[0]["gross_merchandise_sales_gbp"] = "1.125000001"
            rows[0]["identified_gross_sales_share"] = "0.999999999"
            rows[0]["purchase_invoice_count"] = "2"
            return rows, COLUMNS
        result = self.compare_rows(mutate)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["field_mismatches"]), 3)
        self.assertTrue(result["identity_errors"])

    def test_comparison_rejects_duplicate_month_or_wrong_schema(self):
        def duplicate(rows):
            rows[1] = rows[0].copy()
            return rows, COLUMNS
        self.assertEqual(self.compare_rows(duplicate)["status"], "failed")
        def schema(rows):
            return [{key: value for key, value in row.items() if key != COLUMNS[-1]} for row in rows], COLUMNS[:-1]
        self.assertEqual(self.compare_rows(schema)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
