"""Synthetic comparison edge cases; never retailer or cloud evidence."""

import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from compare_category_controls import COLUMNS, compare


class CategoryComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.expected = Path(self.temporary.name) / "expected.csv"
        self.observed = Path(self.temporary.name) / "observed.csv"
        self.rows = [{**dict.fromkeys(COLUMNS, "0"), "period": f"synthetic_{index:02d}", "item_category": "synthetic", "line_status": "synthetic", "rows": "1", "signed_amount_gbp": "0.001"} for index in range(63)]
        self.write(self.expected, self.rows)

    def write(self, path, rows, columns=COLUMNS):
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def result(self):
        return compare(self.expected, self.observed, "synthetic-date")

    def test_reordered_rows_columns_and_trailing_zeros_match_without_changing_inputs(self):
        rows = [{**row, "signed_amount_gbp": "0.001000000"} for row in reversed(self.rows)]
        self.write(self.observed, rows, list(reversed(COLUMNS)))
        before = (self.expected.read_bytes(), self.observed.read_bytes())
        result = self.result()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["numeric_comparisons_total"], 504)
        self.assertTrue(result["both_input_files_unchanged"])
        self.assertEqual(before, (self.expected.read_bytes(), self.observed.read_bytes()))

    def test_small_decimal_difference_cannot_disappear_in_float_rounding(self):
        self.rows[0]["signed_amount_gbp"] = "1234567890123456789012345678.123456789"
        self.write(self.expected, self.rows)
        rows = [row.copy() for row in self.rows]
        rows[0]["signed_amount_gbp"] = "1234567890123456789012345678.123456790"
        self.write(self.observed, rows)
        result = self.result()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["mismatches"]), 1)
        self.assertEqual(result["mismatches"][0]["field"], "signed_amount_gbp")
        self.assertEqual(result["mismatches"][0]["key"][0], "synthetic_00")

    def test_count_and_nonfinite_money_fail_with_field_details(self):
        rows = [row.copy() for row in self.rows]
        rows[0]["rows"] = "2"
        rows[1]["gross_merchandise_amount"] = "NaN"
        self.write(self.observed, rows)
        result = self.result()
        self.assertEqual(result["status"], "failed")
        self.assertEqual({item["field"] for item in result["mismatches"]}, {"rows", "gross_merchandise_amount"})

    def test_same_size_with_missing_and_extra_keys_fails(self):
        rows = [row.copy() for row in self.rows]
        rows[0]["period"] = "synthetic_extra"
        self.write(self.observed, rows)
        result = self.result()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["missing_keys"][0][0], "synthetic_00")
        self.assertEqual(result["extra_keys"][0][0], "synthetic_extra")

    def test_duplicate_keys_and_missing_schema_column_fail(self):
        rows = [row.copy() for row in self.rows]
        rows[1] = rows[0].copy()
        self.write(self.observed, rows)
        result = self.result()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["observed"]["duplicate_keys"]), 1)
        columns = COLUMNS[:-1]
        self.write(self.observed, [{key: row[key] for key in columns} for row in self.rows], columns)
        result = self.result()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["observed"]["schema_matches"])


if __name__ == "__main__":
    unittest.main()
