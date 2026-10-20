"""Read-only keyed comparison of customer-history exports, including NULL semantics."""

import argparse
from collections import Counter
import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from compare_category_controls import sha256
from first_observed_purchases import (
    BOOL_COLUMNS, COLUMNS, CUTOFF, DATE_COLUMNS, DAY_COLUMN, SOURCE_START, history_controls,
)


def read_csv(path):
    before = sha256(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        rows = list(reader)
    keys = [row.get("customer_id") for row in rows]
    info = {"path": path.as_posix(), "sha256_before": before, "bytes": path.stat().st_size,
            "column_count": len(headers), "data_rows": len(rows),
            "schema_matches": len(headers) == len(COLUMNS) and set(headers) == set(COLUMNS),
            "duplicate_customer_ids": [key for key, count in Counter(keys).items() if count > 1],
            "invalid_customer_key_rows": [index + 2 for index, key in enumerate(keys)
                                          if not key or key != key.strip()],
            "malformed_records": [index + 2 for index, row in enumerate(rows)
                                  if None in row or any(value is None for value in row.values())]}
    return info, {row.get("customer_id"): row for row in rows}


def typed(text, field):
    optional = {"first_later_day_purchase_date", DAY_COLUMN, "repeat_purchase_within_90_days"}
    if text == "" and field in optional:
        return None
    if field in DATE_COLUMNS:
        value = date.fromisoformat(text)
        if value.isoformat() != text:
            raise ValueError("Dates must use YYYY-MM-DD")
        return value
    if field in BOOL_COLUMNS:
        if text.lower() not in ("true", "false"):
            raise ValueError("Boolean must be true or false; only ineligible outcomes may be blank")
        return text.lower() == "true"
    value = Decimal(text)
    if not value.is_finite() or value != value.to_integral_value() or value < 1:
        raise ValueError("Days must be a positive integer or blank")
    return int(value)


def row_errors(values):
    first, cohort, later = [values[field] for field in DATE_COLUMNS]
    elapsed = values[DAY_COLUMN]
    eligible, outcome = [values[field] for field in BOOL_COLUMNS]
    correct_eligibility = first + timedelta(days=90) <= CUTOFF
    correct_outcome = None if not correct_eligibility else elapsed is not None and 1 <= elapsed <= 90
    checks = {"first_date_in_history": SOURCE_START <= first <= CUTOFF,
              "cohort_month": cohort == first.replace(day=1),
              "later_date_and_days_null_together": (later is None) == (elapsed is None),
              "later_day_and_gap": later is None or (
                  first < later <= CUTOFF and elapsed == (later - first).days),
              "full_followup_eligibility": eligible == correct_eligibility,
              "outcome_and_ineligible_null": outcome == correct_outcome}
    return [name for name, passed in checks.items() if not passed]


def compare(expected_path, observed_path):
    expected_info, expected = read_csv(expected_path)
    observed_info, observed = read_csv(observed_path)
    structural, mismatches, semantic = [], [], []
    for label, info in (("expected", expected_info), ("observed", observed_info)):
        if (not info["schema_matches"] or info["duplicate_customer_ids"]
                or info["invalid_customer_key_rows"] or info["malformed_records"]):
            structural.append(label)
    missing = sorted(set(expected) - set(observed), key=str)
    extra = sorted(set(observed) - set(expected), key=str)
    if missing or extra or expected_info["data_rows"] != observed_info["data_rows"]:
        structural.append("customer_population")
    compared = nulls = 0
    normalized = {"expected": [], "observed": []}
    if not structural:
        for customer in sorted(expected):
            parsed = {}
            for label, rows in (("expected", expected), ("observed", observed)):
                parsed[label] = {}
                for field in COLUMNS[1:]:
                    try:
                        parsed[label][field] = typed(rows[customer][field], field)
                    except (ValueError, InvalidOperation, TypeError) as error:
                        semantic.append({"file": label, "customer_id": customer, "field": field,
                                         "error": str(error)})
                if len(parsed[label]) == len(COLUMNS) - 1:
                    errors = row_errors(parsed[label])
                    if errors:
                        semantic.append({"file": label, "customer_id": customer, "failed_checks": errors})
                    normalized[label].append({"customer_id": customer, **{
                        field: "" if value is None else str(value).lower() if isinstance(value, bool)
                        else value.isoformat() if isinstance(value, date) else str(value)
                        for field, value in parsed[label].items()}})
            for field in COLUMNS[1:]:
                compared += 1
                if field not in parsed["expected"] or field not in parsed["observed"]:
                    continue
                left, right = parsed["expected"][field], parsed["observed"][field]
                nulls += left is None
                if left != right:
                    mismatches.append({"customer_id": customer, "field": field,
                                       "expected": expected[customer][field], "observed": observed[customer][field]})
    controls = {label: history_controls(rows) for label, rows in normalized.items()}
    for path, info in ((expected_path, expected_info), (observed_path, observed_info)):
        info["sha256_after"] = sha256(path)
        info["unchanged"] = info["sha256_before"] == info["sha256_after"]
    passed = (not structural and not mismatches and not semantic
              and expected_info["unchanged"] and observed_info["unchanged"]
              and controls["expected"] == controls["observed"]
              and compared == len(expected) * (len(COLUMNS) - 1))
    return {"status": "passed" if passed else "failed", "expected": expected_info, "observed": observed_info,
            "structural_errors": structural, "missing_customer_ids": missing, "extra_customer_ids": extra,
            "field_comparisons": compared, "nonnull_field_comparisons": compared - nulls,
            "null_field_comparisons": nulls, "field_mismatches": mismatches,
            "semantic_errors": semantic, "history_controls": controls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, default=Path("reports/first_observed_purchases_expected.csv"))
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/first_observed_purchases_comparison.json"))
    args = parser.parse_args()
    controls_path = Path("reports/first_observed_purchases_local_controls.json")
    evidence = json.loads(controls_path.read_text(encoding="utf-8"))
    inputs = dict(evidence["input_sha256_before_and_after"])
    inputs[evidence["output_path"]] = evidence["output_sha256"]
    inputs[controls_path.as_posix()] = sha256(controls_path)
    if args.expected.resolve() != Path(evidence["output_path"]).resolve():
        raise ValueError("Use the expected CSV recorded in the local control manifest")
    protected = {Path(name).resolve() for name in inputs} | {args.expected.resolve(), args.observed.resolve()}
    if args.output.resolve() in protected:
        raise ValueError("Comparison output cannot overwrite inputs or evidence")
    if args.observed.resolve() in {Path(name).resolve() for name in inputs}:
        raise ValueError("Observed export must be separate from local inputs")
    for name, digest in inputs.items():
        if sha256(Path(name)) != digest:
            raise ValueError(f"Input or evidence changed: {name}")
    result = compare(args.expected, args.observed)
    control_errors = []
    if (result["expected"]["data_rows"] != evidence["expected_rows"]
            or result["history_controls"]["expected"] != evidence["history_controls"]):
        control_errors.append("Expected customer population/history controls differ from preparation")
    if result["status"] == "passed" and (
            result["nonnull_field_comparisons"] != evidence["expected_nonnull_field_cells"]
            or result["null_field_comparisons"] != evidence["expected_null_field_cells"]):
        control_errors.append("Field comparison counts differ from preparation")
    for name, digest in inputs.items():
        if sha256(Path(name)) != digest:
            control_errors.append(f"Input changed during comparison: {name}")
    if control_errors:
        result["status"] = "failed"
    result.update({"compared_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                   "control_errors": control_errors, "local_controls_path": controls_path.as_posix(),
                   "input_sha256_before_and_after": inputs,
                   "cloud_job_metadata": "Unconfirmed unless separately supplied by the owner.",
                   "execution": "Local comparison of owner-supplied CSV; no cloud query or dry run.",
                   "milestone_4": "in progress; cohort summaries and interpretation remain pending"})
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{result['status'].upper()}: {result['observed']['data_rows']} customer rows; "
          f"{result['field_comparisons']} fields; {len(result['field_mismatches'])} mismatches; "
          f"{len(result['semantic_errors'])} semantic errors. Evidence: {args.output.as_posix()}")
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
