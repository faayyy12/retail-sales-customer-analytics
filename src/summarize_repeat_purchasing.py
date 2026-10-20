"""Reproduce descriptive repeat findings from verified CSVs; no cloud access."""

import csv
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cohort_purchase_timing import check_hashes, load_histories, REQUIRED_TOTALS
from compare_category_controls import sha256
from monthly_sales_baseline import ratio
from review_purchase_concentration import median


def rate(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "exact_fraction": str(Fraction(numerator, denominator)) if denominator else None,
            "export_precision_fraction": str(ratio(numerator, denominator)) if denominator else None}


def summarize(histories, cohorts):
    eligible = [row for row in histories if row["has_full_90_day_followup"].lower() == "true"]
    repeaters = [row for row in eligible if row["repeat_purchase_within_90_days"].lower() == "true"]
    gaps = [Decimal(row["days_to_first_later_day_purchase"]) for row in repeaters]
    counts = {"total_customers": len(histories), "eligible_customers": len(eligible),
              "ineligible_customers": len(histories) - len(eligible),
              **{f"eligible_repeaters_{days}d": sum(1 <= gap <= days for gap in gaps) for days in (30, 60, 90)},
              "eligible_nonrepeaters_90d": len(eligible) - len(repeaters)}
    for field, value in counts.items():
        if sum(int(row[field]) for row in cohorts) != value:
            raise ValueError(f"Pooled customer histories and cohort sum differ: {field}")
    for field, value in REQUIRED_TOTALS.items():
        if counts[field] != value:
            raise ValueError(f"Required population control differs: {field}")
    pooled_rates = {f"day_{days}": rate(counts[f"eligible_repeaters_{days}d"], len(eligible)) for days in (30, 60, 90)}
    first_30 = counts["eligible_repeaters_30d"]
    first_60 = counts["eligible_repeaters_60d"]
    rest = [row for row in eligible if row["cohort_month"] != "2009-12-01"]
    rest_repeaters = sum(row["repeat_purchase_within_90_days"].lower() == "true" for row in rest)
    return {"population_totals": counts, "pooled_rates": pooled_rates,
            "first_repeat_day_intervals": {"days_1_30": first_30, "days_31_60": first_60 - first_30,
                                           "days_61_90": len(gaps) - first_60},
            "pooled_conditional_median_days": str(median(gaps)),
            "pooled_conditional_median_population": "All 2305 fully eligible customers repeating within 90 days, calculated directly from verified customer gaps; not an average of cohort medians or a new cloud result row.",
            "repeaters_first_repeating_after_day_30": rate(len(gaps) - first_30, len(gaps)),
            "excluding_december_2009_sensitivity": {
                "rate_90d": rate(rest_repeaters, len(rest)),
                "definition": "Descriptive subset: remove the 951 first-month customers from the pooled calculation only. Original output and records remain unchanged. This is not an adjusted acquisition estimate; later first-observed customers are not known acquisitions either."},
            "unconfirmed_job_metadata": "Cloud execution metadata are not included in the saved analytical exports."}


def main():
    proof_path = Path("reports/cohort_purchase_timing_comparison.json")
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    if proof["status"] != "passed" or proof["field_comparisons"] != 288:
        raise ValueError("Cohort export comparison must pass first")
    if any(proof[field] for field in ("structural_errors", "field_mismatches", "semantic_errors", "control_errors")):
        raise ValueError("Cohort comparison has unresolved errors")
    inputs = dict(proof["input_sha256_before_and_after"])
    inputs[proof_path.as_posix()] = sha256(proof_path)
    inputs["src/summarize_repeat_purchasing.py"] = sha256(Path(__file__))
    for side in ("expected", "observed"):
        info = proof[side]
        if not info["unchanged"] or info["sha256_before"] != info["sha256_after"]:
            raise ValueError("Cohort CSVs were not preserved")
        inputs[info["path"]] = info["sha256_after"]
    check_hashes(inputs)
    customers = load_histories(Path("reports/bigquery_first_observed_purchases.csv"))
    with Path(proof["observed"]["path"]).open(encoding="utf-8-sig", newline="") as stream:
        cohorts = list(csv.DictReader(stream))
    result = summarize(customers, cohorts)
    check_hashes(inputs)
    result.update({"generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                   "execution": "Local descriptive arithmetic on the verified customer/cohort exports; no cloud query or dry run.",
                   "input_sha256_before_and_after": inputs, "all_inputs_unchanged": True,
                   "interpretation": "First observed is not acquisition; no observed 90-day repeat is not proven churn. Cohort differences are descriptive and do not establish causes or outreach effects."})
    output = Path("reports/repeat_purchasing_summary.json")
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("PASS: pooled rates, disjoint timing intervals and customer/cohort totals reconcile; input hashes unchanged.")
    print(json.dumps({key: result[key] for key in ("population_totals", "pooled_rates", "pooled_conditional_median_days")}, indent=2))


if __name__ == "__main__":
    main()
