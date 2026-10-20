"""Compare a manual cohort export exactly, retaining NULL rates and medians."""

import argparse
from collections import Counter
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cohort_purchase_timing import (
    COLUMNS, COUNT_COLUMNS, FLAG_COLUMN, MEDIAN_COLUMN, MONTHS, RATE_COLUMNS,
    REQUIRED_TOTALS, SMALL_DENOMINATOR, check_hashes, summary_controls,
)
from compare_category_controls import sha256
from monthly_sales_baseline import ratio


def read_csv(path):
    before = sha256(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        rows = list(reader)
    keys = [row.get("cohort_month") for row in rows]
    info = {"path": path.as_posix(), "sha256_before": before, "bytes": path.stat().st_size,
            "column_count": len(headers), "data_rows": len(rows),
            "schema_matches": len(headers) == len(COLUMNS) and set(headers) == set(COLUMNS),
            "duplicate_cohorts": [key for key, count in Counter(keys).items() if count > 1],
            "missing_cohorts": sorted(set(MONTHS) - set(keys)),
            "extra_cohorts": sorted(set(keys) - set(MONTHS), key=str),
            "malformed_records": [index + 2 for index, row in enumerate(rows)
                                  if None in row or any(value is None for value in row.values())]}
    return info, {row.get("cohort_month"): row for row in rows}


def typed(text, field):
    if text == "" and field in RATE_COLUMNS + [MEDIAN_COLUMN]:
        return None
    if field == FLAG_COLUMN:
        if text.lower() not in ("true", "false"):
            raise ValueError("Small-denominator flag must be TRUE/FALSE")
        return text.lower() == "true"
    value = Decimal(text)
    if not value.is_finite() or value < 0:
        raise ValueError("Numeric value must be finite and nonnegative")
    if field in COUNT_COLUMNS and value != value.to_integral_value():
        raise ValueError("Count must be an integer")
    return value


def row_errors(month, values):
    total, eligible, ineligible = [values[field] for field in COUNT_COLUMNS[:3]]
    repeats = [values[f"eligible_repeaters_{days}d"] for days in (30, 60, 90)]
    median = values[MEDIAN_COLUMN]
    checks = {"eligibility_split": total == eligible + ineligible,
              "nested_repeat_windows": 0 <= repeats[0] <= repeats[1] <= repeats[2] <= eligible,
              "eligible_90d_outcome_split": eligible == repeats[2] + values["eligible_nonrepeaters_90d"],
              "small_denominator_flag": values[FLAG_COLUMN] == (eligible < SMALL_DENOMINATOR),
              "early_cohorts_fully_eligible": month >= "2011-09-01" or eligible == total,
              "late_cohorts_ineligible": month <= "2011-09-01" or eligible == 0,
              "conditional_median": median is None if repeats[2] == 0 else (
                  median is not None and 1 <= median <= 90 and median * 2 == (median * 2).to_integral_value())}
    for days, count in zip((30, 60, 90), repeats):
        checks[f"rate_{days}d_uses_same_eligible_denominator"] = values[f"repeat_rate_{days}d"] == ratio(count, eligible)
    return [name for name, passed in checks.items() if not passed]


def compare(expected_path, observed_path, required_totals=REQUIRED_TOTALS):
    expected_info, expected = read_csv(expected_path)
    observed_info, observed = read_csv(observed_path)
    structural, mismatches, semantic, control_errors = [], [], [], []
    for label, info in (("expected", expected_info), ("observed", observed_info)):
        if (not info["schema_matches"] or info["data_rows"] != len(MONTHS) or info["duplicate_cohorts"]
                or info["missing_cohorts"] or info["extra_cohorts"] or info["malformed_records"]):
            structural.append(label)
    compared = nulls = 0
    controls = {}
    if not structural:
        normalized = {"expected": [], "observed": []}
        for month in MONTHS:
            parsed = {}
            for label, rows in (("expected", expected), ("observed", observed)):
                parsed[label] = {}
                for field in COLUMNS[1:]:
                    try:
                        parsed[label][field] = typed(rows[month][field], field)
                    except (ValueError, InvalidOperation, TypeError) as error:
                        semantic.append({"file": label, "cohort_month": month, "field": field, "error": str(error)})
                if len(parsed[label]) == len(COLUMNS) - 1:
                    errors = row_errors(month, parsed[label])
                    if errors:
                        semantic.append({"file": label, "cohort_month": month, "failed_checks": errors})
                    normalized[label].append({"cohort_month": month, **{
                        field: "" if value is None else str(value).lower() if isinstance(value, bool)
                        else str(int(value)) if field in COUNT_COLUMNS else str(value)
                        for field, value in parsed[label].items()}})
            for field in COLUMNS[1:]:
                compared += 1
                if field not in parsed["expected"] or field not in parsed["observed"]:
                    continue
                left, right = parsed["expected"][field], parsed["observed"][field]
                nulls += left is None
                if left != right:
                    mismatches.append({"cohort_month": month, "field": field,
                                       "expected": expected[month][field], "observed": observed[month][field]})
        for label, rows in normalized.items():
            if len(rows) == len(MONTHS):
                controls[label] = summary_controls(rows)
                for field, value in required_totals.items():
                    if controls[label]["totals"][field] != value:
                        control_errors.append({"file": label, "field": field, "expected": value,
                                               "observed": controls[label]["totals"][field]})
    for path, info in ((expected_path, expected_info), (observed_path, observed_info)):
        info["sha256_after"] = sha256(path)
        info["unchanged"] = info["sha256_before"] == info["sha256_after"]
    passed = (not structural and not mismatches and not semantic and not control_errors
              and expected_info["unchanged"] and observed_info["unchanged"]
              and controls.get("expected") == controls.get("observed")
              and compared == len(MONTHS) * (len(COLUMNS) - 1))
    return {"status": "passed" if passed else "failed", "expected": expected_info, "observed": observed_info,
            "structural_errors": structural, "field_comparisons": compared,
            "nonnull_field_comparisons": compared - nulls, "null_field_comparisons": nulls,
            "field_mismatches": mismatches, "semantic_errors": semantic, "control_errors": control_errors,
            "summary_controls": controls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/cohort_purchase_timing_comparison.json"))
    args = parser.parse_args()
    evidence_path = Path("reports/cohort_purchase_timing_local_controls.json")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    expected = Path(evidence["output_path"])
    inputs = dict(evidence["input_sha256_before_and_after"])
    inputs[expected.as_posix()] = evidence["output_sha256"]
    inputs[evidence_path.as_posix()] = sha256(evidence_path)
    protected = {Path(name).resolve() for name in inputs}
    if args.observed.resolve() in protected or args.output.resolve() in protected | {args.observed.resolve()}:
        raise ValueError("Observed CSV must be separate; comparison output cannot overwrite inputs/evidence")
    check_hashes(inputs)
    result = compare(expected, args.observed)
    if result["status"] == "passed" and (
            result["summary_controls"]["expected"] != evidence["summary_controls"]
            or result["nonnull_field_comparisons"] != evidence["expected_nonnull_field_cells"]
            or result["null_field_comparisons"] != evidence["expected_null_field_cells"]):
        result["control_errors"].append("Preparation controls or field counts differ")
        result["status"] = "failed"
    check_hashes(inputs)
    result.update({"compared_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                   "input_sha256_before_and_after": inputs,
                   "arithmetic": "Exact Decimal equality for integer counts, explicit 9-place rates and exact conditional medians; NULL distinct from zero.",
                   "execution": "Local comparison of owner-supplied CSV only; no cloud query or dry run.",
                   "cloud_job_metadata": "Unconfirmed unless separately supplied by owner.",
                   "milestone_4": "in progress; interpretation/completion not automatic"})
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{result['status'].upper()}: {result['observed']['data_rows']} cohorts; "
          f"{result['field_comparisons']} fields; {len(result['field_mismatches'])} mismatches. Evidence: {args.output}")
    if result["status"] != "passed":
        print(json.dumps({key: result[key] for key in ("structural_errors", "field_mismatches", "semantic_errors", "control_errors")}, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
