"""Synthetic frozen-rule, boundary, empty-group and exact export checks."""

import csv
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from compare_customer_segments import compare
from customer_segments import (COLUMNS, COUNT_COLUMNS, FEATURES, FLAG_COLUMN, MONEY_COLUMN, RATIO_COLUMNS,
                               aggregate, assign, load_rules, scenario_controls)


def customer(identifier, tenure, recency, days, gross, count=0, amount="0"):
    return {"customer_id": identifier, "observed_tenure_days": str(tenure), "recency_days": str(recency),
            "purchase_day_count": str(days), "purchase_invoice_count": str(days + 1),
            "gross_merchandise_purchase_amount_gbp": str(gross), "subsequent_90d_purchase": str(count > 0).lower(),
            "subsequent_90d_purchase_invoice_count": str(count), "subsequent_90d_gross_purchase_amount_gbp": amount}


class CustomerSegmentsTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rules()
        self.base = self.rules["scenarios"][0]["thresholds"]
        self.records = [customer("A", 60, 60, 1, 10), customer("B", 200, 10, 3, 2000, 2, "1.005"),
                        customer("C", 200, 90, 2, 500, 1, "2.125"), customer("D", 200, 91, 2, 600),
                        customer("E", 200, 91, 1, 250, 1, "0.125")]
        self.totals = {"customer_count": 5, "subsequent_purchasing_customers": 3,
                       "subsequent_invoice_count": 4, MONEY_COLUMN: "3.255"}

    def test_five_exhaustive_groups_once_per_customer_per_scenario(self):
        rows, labels, _ = aggregate(self.records, self.rules)
        self.assertEqual(len(rows), 35)
        self.assertEqual(list(labels["base"].values()), [segment["id"] for segment in self.rules["segments"]])
        self.assertTrue(all(len(scenario) == 5 for scenario in labels.values()))
        self.assertTrue(all(totals == self.totals for totals in scenario_controls(rows).values()))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            aggregate(self.records + self.records[:1], self.rules)

    def test_tenure_guard_precedes_recency_and_exact_endpoints_are_included(self):
        row = customer("A", 90, 80, 1, 100)
        tighter = {**self.base, "recency_days": 75}
        self.assertEqual(assign(row, tighter), "recently_first_observed")
        row = customer("A", 91, 90, 3, 2000)
        self.assertEqual(assign(row, self.base), "recent_established_higher_spend_repeat")
        row["recency_days"] = "91"
        self.assertEqual(assign(row, self.base), "less_recent_multi_day")

    def test_gross_and_distinct_day_thresholds_not_same_day_invoice_counts(self):
        row = customer("A", 200, 10, 3, "1999.999")
        self.assertEqual(assign(row, self.base), "other_recent_established")
        row["gross_merchandise_purchase_amount_gbp"] = "2000"
        self.assertEqual(assign(row, self.base), "recent_established_higher_spend_repeat")
        row["purchase_day_count"] = "2"
        row["purchase_invoice_count"] = "500"
        self.assertEqual(assign(row, self.base), "other_recent_established")
        row = customer("A", 200, 100, 1, 1000)
        row["purchase_invoice_count"] = "500"
        self.assertEqual(assign(row, self.base), "less_recent_single_day")

    def test_outcomes_not_required_by_assignment_and_perturbation_never_changes_groups(self):
        projected = [{field: row[field] for field in FEATURES} for row in self.records]
        self.assertEqual([assign(row, self.base) for row in projected],
                         [assign(row, self.base) for row in self.records])
        changed = [{**row, "subsequent_90d_purchase": "true", "subsequent_90d_purchase_invoice_count": "999",
                    "subsequent_90d_gross_purchase_amount_gbp": "999999999.999"} for row in self.records]
        self.assertEqual(aggregate(self.records, self.rules)[1], aggregate(changed, self.rules)[1])

    def test_sensitivity_variants_change_one_setting_and_keep_base_fixed(self):
        self.assertEqual(len(self.rules["scenarios"]), 7)
        for scenario in self.rules["scenarios"][1:]:
            self.assertEqual(sum(scenario["thresholds"][key] != value for key, value in self.base.items()), 1)
        self.assertEqual(self.base, {"recency_days": 90, "recent_tenure_days": 90,
                                    "core_purchase_days": 3, "core_gross_gbp": "2000"})

    def test_empty_groups_stay_null_rates_and_small_flag_boundary(self):
        rows, _, _ = aggregate(self.records[:1], self.rules)
        for row in rows:
            if row["customer_count"] == "0":
                self.assertEqual(row["subsequent_purchase_rate"], "")
                self.assertEqual(row["subsequent_gross_purchases_per_customer_gbp"], "")
                self.assertEqual(row[MONEY_COLUMN], "0")
                self.assertEqual(row[FLAG_COLUMN], "true")
        for n, flag in ((29, "true"), (30, "false")):
            records = [{**self.records[0], "customer_id": str(i)} for i in range(n)]
            rows, _, _ = aggregate(records, self.rules)
            self.assertEqual(rows[0][FLAG_COLUMN], flag)

    def compare_fixture(self, mutate, records=None):
        records = self.records if records is None else records
        rows, _, _ = aggregate(records, self.rules)
        totals = scenario_controls(rows)["base"]
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
            result = compare(expected, observed, self.rules, totals)
            self.assertEqual(before, [path.read_bytes() for path in (expected, observed)])
            return result

    def test_comparison_accepts_order_scale_and_boolean_case(self):
        def mutate(rows):
            for row in rows:
                for field in COUNT_COLUMNS + RATIO_COLUMNS + [MONEY_COLUMN]:
                    if row[field]:
                        row[field] = format(Decimal(row[field]), ".12f")
                row[FLAG_COLUMN] = row[FLAG_COLUMN].upper()
            return list(reversed(rows)), list(reversed(COLUMNS))
        result = self.compare_fixture(mutate)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["field_comparisons"], 315)

    def test_comparison_detects_tiny_money_wrong_rate_and_empty_null_zero(self):
        def mutate(rows):
            rows[0][MONEY_COLUMN] = "0.000000001"
            rows[1]["subsequent_purchase_rate"] = "0.999999999"
            rows[1]["subsequent_gross_purchases_per_customer_gbp"] = "0"
            return rows, COLUMNS
        result = self.compare_fixture(mutate)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["field_mismatches"]), 3)
        self.assertTrue(result["semantic_errors"])
        def null_change(rows):
            rows[1]["subsequent_purchase_rate"] = "0"
            return rows, COLUMNS
        self.assertEqual(self.compare_fixture(null_change, self.records[:1])["status"], "failed")

    def test_comparison_rejects_missing_extra_duplicate_schema_and_nonfinite_fields(self):
        mutations = [lambda rows: (rows[:-1], COLUMNS), lambda rows: (rows + rows[:1], COLUMNS),
                     lambda rows: (rows + [{**rows[0], "scenario_id": "UNFROZEN"}], COLUMNS),
                     lambda rows: ([{**row, "extra": "x"} for row in rows], COLUMNS + ["extra"]),
                     lambda rows: ([{**row, MONEY_COLUMN: "NaN"} for row in rows], COLUMNS)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                result = self.compare_fixture(mutation)
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["structural_errors"] or result["semantic_errors"])


if __name__ == "__main__":
    unittest.main()
