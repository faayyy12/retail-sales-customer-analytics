"""Synthetic cohort denominators, timing medians and export checks."""

import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cohort_purchase_timing import aggregate, COLUMNS, FLAG_COLUMN, MEDIAN_COLUMN, MONTHS
from compare_cohort_purchase_timing import compare
from first_observed_purchases import build_customer_histories


def histories(first, gaps):
    invoices = []
    for number, gap in enumerate(gaps):
        customer = f"{first}-{number}"
        invoices.append({"customer_id": customer, "invoice_date": date.fromisoformat(first)})
        if gap is not None:
            invoices.append({"customer_id": customer, "invoice_date": date.fromisoformat(first) + timedelta(days=gap)})
    return build_customer_histories(invoices)[0]


class CohortPurchaseTimingTests(unittest.TestCase):
    def test_inclusive_nested_windows_share_full_90_day_denominator(self):
        rows = aggregate(histories("2011-04-01", [1, 30, 31, 60, 61, 90, 91, None]))
        row = next(row for row in rows if row["cohort_month"] == "2011-04-01")
        self.assertEqual([row[f"eligible_repeaters_{days}d"] for days in (30, 60, 90)], ["2", "4", "6"])
        self.assertEqual(row["eligible_nonrepeaters_90d"], "2")
        self.assertEqual([Decimal(row[f"repeat_rate_{days}d"]) for days in (30, 60, 90)],
                         [Decimal("0.25"), Decimal("0.5"), Decimal("0.75")])
        self.assertEqual(Decimal(row[MEDIAN_COLUMN]), Decimal("45.5"))

    def test_partial_september_excludes_ineligible_even_when_they_repeat(self):
        source = histories("2011-09-01", [1]) + histories("2011-09-02", [1, None])
        row = next(row for row in aggregate(source) if row["cohort_month"] == "2011-09-01")
        self.assertEqual([row[field] for field in ("total_customers", "eligible_customers", "ineligible_customers")],
                         ["3", "1", "2"])
        self.assertEqual(Decimal(row["repeat_rate_30d"]), Decimal(1))
        self.assertEqual(Decimal(row[MEDIAN_COLUMN]), Decimal(1))

    def test_all_months_remain_visible_zero_eligibility_has_null_rates_and_median(self):
        rows = aggregate(histories("2009-12-01", [30]) + histories("2011-10-01", [1]))
        self.assertEqual([row["cohort_month"] for row in rows], MONTHS)
        self.assertEqual(len(rows), 24)
        for row in rows:
            if row["eligible_customers"] == "0":
                self.assertEqual([row[f"repeat_rate_{days}d"] for days in (30, 60, 90)], ["", "", ""])
                self.assertEqual(row[MEDIAN_COLUMN], "")
        self.assertEqual(rows[0]["total_customers"], "1")

    def test_conditional_median_odd_even_and_no_repeaters(self):
        cases = [([1, 20, 90, 91, None], "20"), ([30, 31, 150, None], "30.5"), ([91, None], "")]
        for gaps, expected in cases:
            with self.subTest(gaps=gaps):
                row = next(row for row in aggregate(histories("2010-01-01", gaps))
                           if row["cohort_month"] == "2010-01-01")
                self.assertEqual(row[MEDIAN_COLUMN], expected)

    def test_small_flag_boundary_and_duplicate_customer_guard(self):
        for size, flag in ((0, "true"), (29, "true"), (30, "false")):
            rows = aggregate(histories("2011-01-01", [None] * size))
            row = next(row for row in rows if row["cohort_month"] == "2011-01-01")
            self.assertEqual(row[FLAG_COLUMN], flag)
        source = histories("2011-01-01", [1])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            aggregate(source + source)

    def compare_fixture(self, mutate):
        rows = aggregate(histories("2010-01-01", [1, 30, 31, None]) +
                         histories("2011-09-01", [1]) + histories("2011-09-02", [1]))
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
            result = compare(expected, observed, required_totals={})
            self.assertEqual(before, [path.read_bytes() for path in (expected, observed)])
            return result

    def test_comparison_accepts_reordering_decimal_scale_and_boolean_case(self):
        def mutate(rows):
            for row in rows:
                row[FLAG_COLUMN] = row[FLAG_COLUMN].upper()
                if row[MEDIAN_COLUMN]:
                    row[MEDIAN_COLUMN] += ".000"
            return list(reversed(rows)), list(reversed(COLUMNS))
        result = self.compare_fixture(mutate)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["field_comparisons"], 288)

    def test_comparison_detects_wrong_denominator_null_zero_and_tiny_median_change(self):
        def mutate(rows):
            for row in rows:
                if row["cohort_month"] == "2011-09-01":
                    row["repeat_rate_30d"] = "0.500000000"
                if row["cohort_month"] == "2011-10-01":
                    row["repeat_rate_90d"] = "0"
                if row["cohort_month"] == "2010-01-01":
                    row[MEDIAN_COLUMN] = "30.000000001"
            return rows, COLUMNS
        result = self.compare_fixture(mutate)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["field_mismatches"]), 3)
        self.assertTrue(result["semantic_errors"])

    def test_comparison_rejects_duplicates_missing_keys_schema_nonfinite_and_fractional_counts(self):
        mutations = [lambda rows: (rows + [rows[0]], COLUMNS),
                     lambda rows: (rows[:-1], COLUMNS),
                     lambda rows: ([{**row, "extra": "1"} for row in rows], COLUMNS + ["extra"]),
                     lambda rows: ([{**row, "total_customers": "0.1"} for row in rows], COLUMNS),
                     lambda rows: ([{**row, "repeat_rate_30d": "NaN"} for row in rows], COLUMNS)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result = self.compare_fixture(mutation)
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["structural_errors"] or result["semantic_errors"])


if __name__ == "__main__":
    unittest.main()
