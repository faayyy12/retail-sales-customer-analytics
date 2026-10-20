"""Exact keyed comparison of frozen segment/sensitivity exports; no cloud access."""

import argparse
from collections import Counter
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cohort_purchase_timing import check_hashes
from compare_category_controls import sha256
from customer_segments import (COLUMNS, COUNT_COLUMNS, FLAG_COLUMN, KEYS, MONEY_COLUMN, RATIO_COLUMNS,
                               load_rules, scenario_controls)
from monthly_sales_baseline import ratio


REQUIRED_TOTALS = {"customer_count": 5224, "subsequent_purchasing_customers": 2236,
                   "subsequent_invoice_count": 5207, MONEY_COLUMN: "2699494.19"}


def read_csv(path, rules):
    before = sha256(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        rows = list(reader)
    keys = [tuple(row.get(field) for field in KEYS) for row in rows]
    required = {(s["id"], g["id"]) for s in rules["scenarios"] for g in rules["segments"]}
    info = {"path": path.as_posix(), "sha256_before": before, "bytes": path.stat().st_size,
            "column_count": len(headers), "data_rows": len(rows),
            "schema_matches": len(headers) == len(COLUMNS) and set(headers) == set(COLUMNS),
            "duplicate_keys": [key for key, n in Counter(keys).items() if n > 1],
            "missing_keys": sorted(required - set(keys)), "extra_keys": sorted(set(keys) - required, key=str),
            "malformed_records": [i + 2 for i, row in enumerate(rows) if None in row or any(v is None for v in row.values())]}
    return info, dict(zip(keys, rows))


def typed(text, field):
    if text == "" and field in RATIO_COLUMNS:
        return None
    if field == FLAG_COLUMN:
        if text.lower() not in ("true", "false"):
            raise ValueError("Flag must be TRUE/FALSE")
        return text.lower() == "true"
    value = Decimal(text)
    if not value.is_finite() or value < 0:
        raise ValueError("Value must be finite and nonnegative")
    if field in COUNT_COLUMNS and value != value.to_integral_value():
        raise ValueError("Count must be integral")
    return value


def row_errors(values, totals):
    n, purchasers, invoices = [values[field] for field in COUNT_COLUMNS]
    gross = values[MONEY_COLUMN]
    checks = {"purchaser_invoice_population": 0 <= purchasers <= n and purchasers <= invoices,
              "zero_outcome_counts_amount": (invoices == 0) == (gross == 0) == (purchasers == 0),
              "empty_group": n > 0 or (invoices == 0 and gross == 0),
              "customer_share": values["customer_share"] == ratio(n, totals["customer_count"]),
              "purchase_rate_denominator": values["subsequent_purchase_rate"] == ratio(purchasers, n),
              "gross_per_customer_denominator": values["subsequent_gross_purchases_per_customer_gbp"] == ratio(gross, n),
              "gross_share": values["subsequent_gross_purchases_share"] == ratio(gross, Decimal(totals[MONEY_COLUMN])),
              "small_group_flag": values[FLAG_COLUMN] == (n < 30)}
    return [name for name, passed in checks.items() if not passed]


def compare(expected_path, observed_path, rules=None, required_totals=REQUIRED_TOTALS):
    rules = rules or load_rules()
    left_info, left = read_csv(expected_path, rules)
    right_info, right = read_csv(observed_path, rules)
    structural, mismatches, semantics, control_errors = [], [], [], []
    for label, info in (("expected", left_info), ("observed", right_info)):
        if not info["schema_matches"] or any(info[key] for key in ("duplicate_keys", "missing_keys", "extra_keys", "malformed_records")):
            structural.append(label)
    compared = nulls = 0
    controls = {}
    parsed = {"expected": {}, "observed": {}}
    with localcontext() as context:
        context.prec = 80
        if not structural:
            normalized = {"expected": [], "observed": []}
            for label, records in (("expected", left), ("observed", right)):
                for key, row in records.items():
                    values = {}
                    for field in COLUMNS[2:]:
                        try:
                            values[field] = typed(row[field], field)
                        except (ValueError, TypeError, InvalidOperation) as error:
                            semantics.append({"file": label, "key": key, "field": field, "error": str(error)})
                    parsed[label][key] = values
                    if len(values) == len(COLUMNS) - 2:
                        normalized[label].append({**dict(zip(KEYS, key)), **{
                            field: "" if value is None else str(value).lower() if isinstance(value, bool)
                            else str(int(value)) if field in COUNT_COLUMNS else str(value) for field, value in values.items()}})
                if len(normalized[label]) == len(records):
                    controls[label] = scenario_controls(normalized[label])
                    for scenario, totals in controls[label].items():
                        if totals != required_totals:
                            control_errors.append({"file": label, "scenario": scenario, "expected": required_totals, "observed": totals})
                    for key, values in parsed[label].items():
                        errors = row_errors(values, controls[label][key[0]])
                        if errors:
                            semantics.append({"file": label, "key": key, "failed_checks": errors})
            for key in sorted(left):
                for field in COLUMNS[2:]:
                    compared += 1
                    if field not in parsed["expected"][key] or field not in parsed["observed"][key]:
                        continue
                    lvalue, rvalue = parsed["expected"][key][field], parsed["observed"][key][field]
                    nulls += lvalue is None
                    if lvalue != rvalue:
                        mismatches.append({"key": key, "field": field, "expected": left[key][field], "observed": right[key][field]})
    for path, info in ((expected_path, left_info), (observed_path, right_info)):
        info["sha256_after"] = sha256(path)
        info["unchanged"] = info["sha256_before"] == info["sha256_after"]
    passed = (not structural and not mismatches and not semantics and not control_errors
              and left_info["unchanged"] and right_info["unchanged"]
              and controls.get("expected") == controls.get("observed")
              and compared == len(left) * (len(COLUMNS) - 2))
    return {"status": "passed" if passed else "failed", "expected": left_info, "observed": right_info,
            "structural_errors": structural, "field_mismatches": mismatches, "semantic_errors": semantics,
            "control_errors": control_errors, "field_comparisons": compared,
            "nonnull_field_comparisons": compared - nulls, "null_field_comparisons": nulls, "scenario_controls": controls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/customer_segments_comparison.json"))
    args = parser.parse_args()
    rules = load_rules()
    control_path = Path("reports/customer_segments_local_controls.json")
    evidence = json.loads(control_path.read_text(encoding="utf-8"))
    expected = Path(evidence["output_path"])
    inputs = dict(evidence["input_sha256_before_and_after"])
    inputs[expected.as_posix()] = evidence["output_sha256"]
    inputs[control_path.as_posix()] = sha256(control_path)
    protected = {Path(name).resolve() for name in inputs}
    if args.observed.resolve() in protected or args.output.resolve() in protected | {args.observed.resolve()}:
        raise ValueError("Observed export must be separate; output cannot overwrite inputs/evidence")
    check_hashes(inputs)
    result = compare(expected, args.observed, rules)
    preparation_match = (result["scenario_controls"].get("expected") == evidence["scenario_controls"]
                         and result["nonnull_field_comparisons"] == evidence["expected_nonnull_field_cells"]
                         and result["null_field_comparisons"] == evidence["expected_null_field_cells"])
    if not preparation_match:
        result["status"] = "failed"
    result["preparation_controls_match"] = preparation_match
    check_hashes(inputs)
    result.update({"compared_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                   "execution": "Local comparison of owner-supplied segment export; no cloud query/dry run.",
                   "input_sha256_before_and_after": inputs, "frozen_rules_sha256": evidence["frozen_rules_sha256"],
                   "outcome_exposure": rules["outcome_exposure"], "base_remains_primary": True,
                   "cloud_job_metadata": "Unconfirmed unless separately supplied by owner.",
                   "milestone_5": "in progress; interpretation/completion review not automatic"})
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{result['status'].upper()}: {result['observed']['data_rows']} rows; {result['field_comparisons']} fields; "
          f"{len(result['field_mismatches'])} mismatches. Evidence: {args.output}")
    if result["status"] != "passed":
        print(json.dumps({key: result[key] for key in ("structural_errors", "field_mismatches", "semantic_errors", "control_errors")}, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
