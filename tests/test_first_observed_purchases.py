"""Synthetic customer-day, follow-up and export edge cases; not retailer evidence."""

import csv
from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cleaning_rules import load_code_rules
from compare_first_observed_purchases import compare, row_errors, typed
from first_observed_purchases import build_customer_histories, COLUMNS, rebuild_purchase_inputs
from test_monthly_sales import source_row


def invoice(customer, day):
    return {"customer_id": customer, "invoice_date": date.fromisoformat(day)}


class FirstObservedTests(unittest.TestCase):
    def histories(self, invoices):
        return build_customer_histories(invoices)[0]

    def test_same_day_invoices_alone_do_not_count(self):
        row = self.histories([invoice("00123", "2010-01-01")] * 3)[0]
        self.assertEqual(row["customer_id"], "00123")
        self.assertEqual(row["first_later_day_purchase_date"], "")
        self.assertEqual(row["days_to_first_later_day_purchase"], "")
        self.assertEqual(row["repeat_purchase_within_90_days"], "false")

    def test_day_one_and_ninety_included_day_ninety_one_retained_but_false(self):
        for later, days, outcome in (("2011-09-01", "1", "true"),
                                     ("2011-11-29", "90", "true"),
                                     ("2011-11-30", "91", "false")):
            with self.subTest(later=later):
                row = self.histories([invoice("A", "2011-08-31"), invoice("A", later)])[0]
                self.assertEqual(row["first_later_day_purchase_date"], later)
                self.assertEqual(row["days_to_first_later_day_purchase"], days)
                self.assertEqual(row["repeat_purchase_within_90_days"], outcome)

    def test_eligibility_boundary_and_ineligible_repeat_remains_null(self):
        rows = self.histories([invoice("A", "2011-09-01"), invoice("A", "2011-11-30"),
                               invoice("B", "2011-09-02"), invoice("B", "2011-09-03"),
                               invoice("C", "2011-11-30")])
        self.assertEqual([row["has_full_90_day_followup"] for row in rows], ["true", "false", "false"])
        self.assertEqual([row["repeat_purchase_within_90_days"] for row in rows], ["true", "", ""])
        self.assertEqual(rows[1]["days_to_first_later_day_purchase"], "1")

    def test_full_history_not_comparable_sales_periods_and_no_december_2011(self):
        rows = self.histories([invoice("A", "2009-12-01"), invoice("A", "2010-01-01"),
                               invoice("B", "2010-12-31"), invoice("B", "2011-01-01"),
                               invoice("C", "2011-08-01"), invoice("C", "2011-12-01"),
                               invoice("D", "2011-12-01"), invoice(None, "2010-01-01")])
        self.assertEqual([row["customer_id"] for row in rows], ["A", "B", "C"])
        self.assertEqual(rows[0]["first_observed_purchase_date"], "2009-12-01")
        self.assertEqual(rows[1]["cohort_month"], "2010-12-01")
        self.assertEqual(rows[2]["first_later_day_purchase_date"], "")

    def test_unordered_invoices_select_earliest_strictly_later_day(self):
        rows, controls = build_customer_histories([invoice("A", "2011-05-01"), invoice("A", "2011-01-01"),
                                                   invoice("A", "2011-03-01"), invoice("A", "2011-01-01")])
        self.assertEqual(rows[0]["first_later_day_purchase_date"], "2011-03-01")
        self.assertEqual(controls["identified_purchase_invoices_through_cutoff"], 4)
        self.assertEqual(controls["unique_customer_purchase_days"], 3)

    def test_reconstruction_preserves_repeated_lines_excludes_credits_overlap_and_anonymous_history(self):
        source = [source_row(), source_row(), source_row(invoice="C123456", quantity="-1"),
                  source_row(invoice="123457", code="POST"), source_row(invoice="123458", customer="__NULL__"),
                  source_row(invoice="123459", timestamp="2010-12-02 00:00:00"),
                  source_row(invoice="123459", timestamp="2010-12-02 00:00:00", sheet="Year 2010-2011")]
        inputs, checks = rebuild_purchase_inputs(source, load_code_rules(), "__NULL__")
        self.assertEqual(checks["excluded_overlap_rows"], 1)
        self.assertEqual(checks["qualifying_lines_all_history"], 4)
        self.assertEqual(len(inputs), 3)
        rows = self.histories(inputs)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["first_later_day_purchase_date"], "2010-12-02")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            rebuild_purchase_inputs([source_row(), source_row(customer="__NULL__")], load_code_rules(), "__NULL__")

    def compare_fixture(self, mutate):
        rows = self.histories([invoice("A", "2011-01-01"), invoice("A", "2011-01-02"),
                               invoice("B", "2011-09-02"), invoice("B", "2011-09-03"),
                               invoice("C", "2011-01-01")])
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            expected, observed = [Path(folder) / name for name in ("expected.csv", "observed.csv")]
            def write(path, values, columns):
                with path.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=columns)
                    writer.writeheader()
                    writer.writerows(values)
            write(expected, rows, COLUMNS)
            changed, columns = mutate([row.copy() for row in rows])
            write(observed, changed, columns)
            before = [path.read_bytes() for path in (expected, observed)]
            result = compare(expected, observed)
            self.assertEqual(before, [path.read_bytes() for path in (expected, observed)])
            return result

    def test_comparison_accepts_order_and_boolean_case_with_exact_ids(self):
        def mutate(rows):
            for row in rows:
                for field in ("has_full_90_day_followup", "repeat_purchase_within_90_days"):
                    row[field] = row[field].upper()
            return list(reversed(rows)), list(reversed(COLUMNS))
        result = self.compare_fixture(mutate)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["field_comparisons"], 18)
        self.assertEqual(result["null_field_comparisons"], 3)

    def test_comparison_distinguishes_null_false_and_detects_date_and_gap_changes(self):
        def mutate(rows):
            rows[1]["repeat_purchase_within_90_days"] = "false"
            rows[0]["days_to_first_later_day_purchase"] = "2"
            rows[0]["cohort_month"] = "2011-01-02"
            return rows, COLUMNS
        result = self.compare_fixture(mutate)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["field_mismatches"]), 3)
        self.assertTrue(result["semantic_errors"])

    def test_comparison_rejects_duplicate_missing_extra_keys_and_schema(self):
        mutations = [lambda rows: (rows + [rows[0]], COLUMNS),
                     lambda rows: (rows[:-1], COLUMNS),
                     lambda rows: (rows + [{**rows[0], "customer_id": "EXTRA"}], COLUMNS),
                     lambda rows: ([{**row, "unexpected": "value"} for row in rows], COLUMNS + ["unexpected"])]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result = self.compare_fixture(mutation)
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["structural_errors"])

    def test_semantic_checks_reject_false_eligibility_out_of_history_and_bad_types(self):
        row = self.histories([invoice("A", "2011-09-01"), invoice("A", "2011-11-30")])[0]
        parsed = {field: typed(row[field], field) for field in COLUMNS[1:]}
        self.assertEqual(row_errors(parsed), [])
        parsed["has_full_90_day_followup"] = False
        self.assertIn("full_followup_eligibility", row_errors(parsed))
        parsed["first_later_day_purchase_date"] = date(2011, 12, 1)
        self.assertIn("later_day_and_gap", row_errors(parsed))
        for text, field in (("0", "days_to_first_later_day_purchase"),
                            ("NaN", "days_to_first_later_day_purchase"),
                            ("1", "has_full_90_day_followup"), ("NULL", "repeat_purchase_within_90_days"),
                            ("20110101", "first_observed_purchase_date")):
            with self.subTest(text=text), self.assertRaises(ValueError):
                typed(text, field)


if __name__ == "__main__":
    unittest.main()
