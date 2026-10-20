"""Local cohort controls from source-backed, export-verified customer histories."""

from collections import defaultdict
import csv
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from compare_category_controls import sha256
from compare_first_observed_purchases import read_csv, row_errors, typed
from first_observed_purchases import COLUMNS as HISTORY_COLUMNS, history_controls
from monthly_sales_baseline import ratio
from review_purchase_concentration import median


MONTHS = ["2009-12-01"] + [f"2010-{month:02d}-01" for month in range(1, 13)] + [
    f"2011-{month:02d}-01" for month in range(1, 12)]
COUNT_COLUMNS = ["total_customers", "eligible_customers", "ineligible_customers",
                 "eligible_repeaters_30d", "eligible_repeaters_60d", "eligible_repeaters_90d",
                 "eligible_nonrepeaters_90d"]
RATE_COLUMNS = [f"repeat_rate_{days}d" for days in (30, 60, 90)]
MEDIAN_COLUMN = "median_days_to_first_later_day_purchase_among_eligible_90d_repeaters"
FLAG_COLUMN = "small_eligible_denominator"
SMALL_DENOMINATOR = 30
COLUMNS = ["cohort_month"] + COUNT_COLUMNS + RATE_COLUMNS + [MEDIAN_COLUMN, FLAG_COLUMN]
REQUIRED_TOTALS = {"total_customers": 5824, "eligible_customers": 5227,
                   "ineligible_customers": 597, "eligible_repeaters_90d": 2305,
                   "eligible_nonrepeaters_90d": 2922}


def aggregate(histories):
    """Count unique customers, with exact sorted-value medians; no SQL windows."""
    cohorts, seen = defaultdict(list), set()
    for row in histories:
        customer = row["customer_id"]
        if not customer or customer != customer.strip() or customer in seen:
            raise ValueError("Missing/invalid or duplicate customer ID")
        seen.add(customer)
        values = {field: typed(row[field], field) for field in HISTORY_COLUMNS[1:]}
        errors = row_errors(values)
        if errors:
            raise ValueError(f"Customer history semantic errors: {customer}: {errors}")
        month = values["cohort_month"].isoformat()
        if month not in MONTHS:
            raise ValueError(f"Customer cohort outside reporting calendar: {month}")
        cohorts[month].append(values)
    output = []
    for month in MONTHS:
        members = cohorts[month]
        eligible = [row for row in members if row["has_full_90_day_followup"]]
        gaps = [row["days_to_first_later_day_purchase"] for row in eligible
                if row["days_to_first_later_day_purchase"] is not None]
        counts = {days: sum(1 <= gap <= days for gap in gaps) for days in (30, 60, 90)}
        repeating_gaps = [Decimal(gap) for gap in gaps if 1 <= gap <= 90]
        if len(repeating_gaps) != sum(row["repeat_purchase_within_90_days"] for row in eligible):
            raise ValueError("90-day repeat counts differ from verified outcome flags")
        values = {"cohort_month": month, "total_customers": len(members),
                  "eligible_customers": len(eligible), "ineligible_customers": len(members) - len(eligible),
                  **{f"eligible_repeaters_{days}d": counts[days] for days in (30, 60, 90)},
                  "eligible_nonrepeaters_90d": len(eligible) - counts[90],
                  **{f"repeat_rate_{days}d": ratio(counts[days], len(eligible)) for days in (30, 60, 90)},
                  MEDIAN_COLUMN: median(repeating_gaps), FLAG_COLUMN: len(eligible) < SMALL_DENOMINATOR}
        output.append({field: "" if values[field] is None else str(values[field]).lower()
                       if isinstance(values[field], bool) else str(values[field]) for field in COLUMNS})
    return output


def summary_controls(rows):
    return {"cohort_rows": len(rows),
            "totals": {field: sum(int(row[field]) for row in rows) for field in COUNT_COLUMNS},
            "cohorts_without_eligible_customers": [row["cohort_month"] for row in rows
                                                  if int(row["eligible_customers"]) == 0],
            "small_denominator_cohorts": [row["cohort_month"] for row in rows if row[FLAG_COLUMN] == "true"],
            "null_rate_cells": sum(row[field] == "" for row in rows for field in RATE_COLUMNS),
            "null_medians": sum(row[MEDIAN_COLUMN] == "" for row in rows)}


def verified_inputs():
    proof_path = Path("reports/first_observed_purchases_comparison.json")
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    if proof["status"] != "passed" or proof["field_comparisons"] != 34944:
        raise ValueError("Customer-level export comparison must have passed")
    for field in ("structural_errors", "missing_customer_ids", "extra_customer_ids",
                  "field_mismatches", "semantic_errors", "control_errors"):
        if proof[field]:
            raise ValueError(f"Customer comparison has outstanding errors: {field}")
    inputs = dict(proof["input_sha256_before_and_after"])
    inputs[proof_path.as_posix()] = sha256(proof_path)
    for side in ("expected", "observed"):
        info = proof[side]
        if not info["unchanged"] or info["sha256_before"] != info["sha256_after"]:
            raise ValueError("Verified customer CSV was not preserved")
        inputs[info["path"]] = info["sha256_after"]
    check_hashes(inputs)
    return proof, inputs


def check_hashes(inputs):
    for name, digest in inputs.items():
        if sha256(Path(name)) != digest:
            raise ValueError(f"Verified input/evidence changed: {name}")


def load_histories(path):
    info, rows = read_csv(path)
    if (not info["schema_matches"] or info["duplicate_customer_ids"] or info["invalid_customer_key_rows"]
            or info["malformed_records"]):
        raise ValueError(f"Invalid customer-history CSV: {path}")
    return list(rows.values())


def main():
    proof, inputs = verified_inputs()
    for name in ("src/cohort_purchase_timing.py", "src/compare_cohort_purchase_timing.py",
                 "sql/09_cohort_purchase_timing.sql", "data/processed/sql/09_cohort_purchase_timing.sql"):
        inputs[name] = sha256(Path(name))
    local = load_histories(Path(proof["expected"]["path"]))
    observed = load_histories(Path(proof["observed"]["path"]))
    if history_controls(local) != proof["history_controls"]["expected"]:
        raise ValueError("Source-backed customer history controls changed")
    rows = aggregate(local)
    if rows != aggregate(observed):
        raise ValueError("Existing verified customer export gives different cohort summaries")
    controls = summary_controls(rows)
    for field, value in REQUIRED_TOTALS.items():
        if controls["totals"][field] != value:
            raise ValueError(f"Cohort/customer population reconciliation differs: {field}")
    check_hashes(inputs)
    output = Path("reports/cohort_purchase_timing_expected.csv")
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    evidence = {"generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                "execution": "Local aggregation of source-backed customer expectations and cross-check against the previously verified customer export. No new cloud query or dry run.",
                "milestone_4": "in progress", "new_cohort_export_comparison": "pending",
                "output_path": output.as_posix(), "output_sha256": sha256(output), "output_bytes": output.stat().st_size,
                "expected_columns": COLUMNS, "expected_rows": len(rows),
                "input_sha256_before_and_after": inputs, "all_inputs_unchanged": True,
                "verified_customer_export_aggregation_matches": True, "summary_controls": controls,
                "required_population_totals": REQUIRED_TOTALS,
                "expected_nonnull_field_cells": sum(row[field] != "" for row in rows for field in COLUMNS[1:]),
                "expected_null_field_cells": sum(row[field] == "" for row in rows for field in COLUMNS[1:]),
                "rate_definition": "Eligible later-day repeaters by days 1-30, 1-60, 1-90 inclusive, each divided by the SAME fully 90-day-eligible cohort population. NULL when no eligible customers. Export rates: 9 decimals, half away from zero; underlying integer fractions retained.",
                "median_definition": "Exact continuous median of first later-day gaps among fully eligible customers repeating by day 90; conditional on repeating. Mean of central two values for even n; NULL if no such repeaters.",
                "small_denominator_threshold": SMALL_DENOMINATOR,
                "small_denominator_definition": "Eligible n < 30, including zero. Display caution, not a statistical significance rule or suppression.",
                "limitations": "December 2009 has no earlier history and may contain longstanding customers; first observed is not acquisition. September 2011 eligibility covers first purchases on September 1 only. No eligible customers in later cohorts does not establish non-repeating or churn. Conditional median is not a median for all customers.",
                "cloud_job_metadata": "Unconfirmed; new cohort query has not been executed or dry-run."}
    Path("reports/cohort_purchase_timing_local_controls.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"PASS: {len(rows)} cohorts x {len(COLUMNS)} columns; all customer totals reconcile. New cohort export comparison pending.")
    print(json.dumps(controls, indent=2))


if __name__ == "__main__":
    main()
