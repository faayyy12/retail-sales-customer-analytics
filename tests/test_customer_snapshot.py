"""Synthetic time-boundary, leakage, Decimal and snapshot-export cases."""

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cleaning_rules import load_code_rules
from compare_customer_snapshot import compare
from customer_snapshot import aggregate, COLUMNS, COUNT_COLUMNS, FEATURE_COLUMNS, MONEY_COLUMNS
from first_observed_purchases import rebuild_purchase_inputs
from test_monthly_sales import source_row


def invoice(number, customer, day, gross="1.125"):
    return {"invoice_no": number, "customer_id": customer, "invoice_date": date.fromisoformat(day),
            "gross": Decimal(gross)}


class CustomerSnapshotTests(unittest.TestCase):
    def test_history_includes_both_decembers_and_snapshot_day_with_explicit_recency_tenure(self):
        rows = aggregate([invoice("A", "00123", "2009-12-01"), invoice("B", "00123", "2010-12-01"),
                          invoice("C", "00123", "2011-08-31")])
        row = rows[0]
        self.assertEqual(row["customer_id"], "00123")
        self.assertEqual(row["first_observed_purchase_date"], "2009-12-01")
        self.assertEqual(row["last_observed_purchase_date"], "2011-08-31")
        self.assertEqual(row["recency_days"], "0")
        self.assertEqual(row["observed_tenure_days"], str((date(2011, 8, 31) - date(2009, 12, 1)).days))
        self.assertEqual(row["purchase_invoice_count"], "3")
        self.assertEqual(row["purchase_day_count"], "3")

    def test_same_day_invoices_count_separately_from_days_and_tenure_includes_inactivity(self):
        row = aggregate([invoice("A", "C", "2011-08-01"), invoice("B", "C", "2011-08-01")])[0]
        self.assertEqual(row["purchase_invoice_count"], "2")
        self.assertEqual(row["purchase_day_count"], "1")
        self.assertEqual(row["observed_tenure_days"], "30")
        self.assertEqual(row["recency_days"], "30")

    def test_outcome_start_end_inclusive_november_30_and_december_excluded(self):
        rows = aggregate([invoice("A", "C", "2011-08-31"), invoice("B", "C", "2011-09-01", "2.001"),
                          invoice("C", "C", "2011-11-29", "3.002"), invoice("D", "C", "2011-11-30", "100"),
                          invoice("E", "C", "2011-12-01", "1000")])
        self.assertEqual(rows[0]["subsequent_90d_purchase"], "true")
        self.assertEqual(rows[0]["subsequent_90d_purchase_invoice_count"], "2")
        self.assertEqual(Decimal(rows[0][MONEY_COLUMNS[1]]), Decimal("5.003"))
        self.assertEqual(Decimal(rows[0][MONEY_COLUMNS[0]]), Decimal("1.125"))

    def test_nonpurchasing_customers_stay_false_zero_anonymous_and_future_only_never_enter(self):
        rows = aggregate([invoice("A", "PAST", "2011-08-31"), invoice("B", "FUTURE", "2011-09-01"),
                          invoice("C", None, "2011-08-31"), invoice("D", None, "2011-09-01")])
        self.assertEqual([row["customer_id"] for row in rows], ["PAST"])
        self.assertEqual(rows[0]["subsequent_90d_purchase"], "false")
        self.assertEqual(rows[0]["subsequent_90d_purchase_invoice_count"], "0")
        self.assertEqual(Decimal(rows[0][MONEY_COLUMNS[1]]), Decimal(0))
        self.assertNotIn("", rows[0].values())

    def test_future_perturbation_leaves_all_features_and_population_identical(self):
        past = [invoice("A", "C", "2010-01-01"), invoice("B", "C", "2011-08-31")]
        base = aggregate(past)
        changed = aggregate(past + [invoice("X", "C", "2011-09-01", "999999999.999"),
                                    invoice("Y", "FUTURE", "2011-09-01")])
        self.assertEqual([[row[field] for field in FEATURE_COLUMNS] for row in base],
                         [[row[field] for field in FEATURE_COLUMNS] for row in changed])
        self.assertNotEqual(base[0][MONEY_COLUMNS[1]], changed[0][MONEY_COLUMNS[1]])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            aggregate(past + past)

    def test_source_classification_overlap_and_legitimate_repeated_lines_are_reused(self):
        source = [source_row(), source_row(), source_row(invoice="C123456", quantity="-1"),
                  source_row(invoice="123457", code="POST"),
                  source_row(invoice="123458", timestamp="2010-12-02 00:00:00"),
                  source_row(invoice="123458", timestamp="2010-12-02 00:00:00", sheet="Year 2010-2011")]
        inputs, checks = rebuild_purchase_inputs(source, load_code_rules(), "__NULL__")
        row = aggregate(inputs)[0]
        self.assertEqual(checks["excluded_overlap_rows"], 1)
        self.assertEqual(row["purchase_invoice_count"], "2")
        self.assertEqual(Decimal(row[MONEY_COLUMNS[0]]), Decimal("3.375"))

    def compare_fixture(self, mutate):
        rows = aggregate([invoice("A", "00123", "2011-08-01"), invoice("B", "00123", "2011-09-01"),
                          invoice("C", "SECOND", "2011-08-31")])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            expected, observed = [Path(folder) / name for name in ("expected.csv", "observed.csv")]
            def write(path, records, columns):
                with path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=columns)
                    writer.writeheader()
                    writer.writerows(records)
            write(expected, rows, COLUMNS)
            changed, columns = mutate([row.copy() for row in rows])
            write(observed, changed, columns)
            before = [path.read_bytes() for path in (expected, observed)]
            result = compare(expected, observed)
            self.assertEqual(before, [path.read_bytes() for path in (expected, observed)])
            return result

    def test_comparison_accepts_order_numeric_scale_and_boolean_case(self):
        def mutate(rows):
            for row in rows:
                for field in COUNT_COLUMNS + MONEY_COLUMNS:
                    row[field] += ".000000" if "." not in row[field] else "000000"
                row["subsequent_90d_purchase"] = row["subsequent_90d_purchase"].upper()
            return list(reversed(rows)), list(reversed(COLUMNS))
        result = self.compare_fixture(mutate)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["field_comparisons"], 26)

    def test_comparison_detects_tiny_money_difference_future_feature_date_and_wrong_zero_outcome(self):
        def mutate(rows):
            rows[0][MONEY_COLUMNS[0]] = "1.125000001"
            rows[0]["last_observed_purchase_date"] = "2011-09-01"
            rows[1]["subsequent_90d_purchase"] = "true"
            return rows, COLUMNS
        result = self.compare_fixture(mutate)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["field_mismatches"]), 3)
        self.assertTrue(result["semantic_errors"])

    def test_comparison_rejects_missing_extra_duplicate_schema_and_null_fields(self):
        mutations = [lambda rows: (rows[:-1], COLUMNS), lambda rows: (rows + [rows[0]], COLUMNS),
                     lambda rows: (rows + [{**rows[0], "customer_id": "FUTURE"}], COLUMNS),
                     lambda rows: ([{**row, "extra": "x"} for row in rows], COLUMNS + ["extra"]),
                     lambda rows: ([{**row, MONEY_COLUMNS[1]: ""} for row in rows], COLUMNS),
                     lambda rows: ([{**row, "recency_days": "0.1"} for row in rows], COLUMNS),
                     lambda rows: ([{**row, MONEY_COLUMNS[0]: "NaN"} for row in rows], COLUMNS)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result = self.compare_fixture(mutation)
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["structural_errors"] or result["semantic_errors"])


if __name__ == "__main__":
    unittest.main()
