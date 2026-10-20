"""Read-only validation of public data, analytical exports and dashboard inputs."""

import argparse
import csv
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path

from cleaning_rules import load_code_rules
from prepare_presentation import (
    PERIOD_COLUMNS, cohort_checks, csv_bytes, period_summary, read_csv,
    segment_partitions,
)

MANIFEST = Path("reports/analysis/input_checksums.json")
BASELINE = Path("reports/bigquery_monthly_sales_baseline.csv")
COMPARISONS = [
    ("cleaning", "compare_category_controls", "reports/cleaning_category_controls.csv", "reports/bigquery_cleaning_category_controls.csv"),
    ("monthly_sales", "compare_monthly_sales", "reports/monthly_sales_expected.csv", str(BASELINE)),
    ("identified_sales", "compare_identified_sales", "reports/identified_sales_expected.csv", "reports/bigquery_identified_sales_breakdown.csv"),
    ("recorded_credits", "compare_recorded_credits", "reports/recorded_credit_expected.csv", "reports/bigquery_recorded_credit_analysis.csv"),
    ("customer_histories", "compare_first_observed_purchases", "reports/first_observed_purchases_expected.csv", "reports/bigquery_first_observed_purchases.csv"),
    ("cohorts", "compare_cohort_purchase_timing", "reports/cohort_purchase_timing_expected.csv", "reports/bigquery_cohort_purchase_timing.csv"),
    ("snapshot", "compare_customer_snapshot", "reports/customer_snapshot_expected.csv", "reports/bigquery_customer_snapshot.csv"),
    ("segments", "compare_customer_segments", "reports/customer_segments_expected.csv", "reports/11_customer_segments.csv"),
]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def check_input_hashes():
    files = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    for name, expected in files.items():
        require(digest(name) == expected, f"Input checksum differs: {name}")
    return files


def compare_exports():
    from importlib import import_module
    results = []
    for label, module, expected, observed in COMPARISONS:
        compare = import_module(module).compare
        args = [Path(expected), Path(observed)]
        if label == "cleaning":
            args.append("saved-data validation")
        elif label in {"identified_sales", "recorded_credits"}:
            args.append(BASELINE)
        result = compare(*args)
        require(result["status"] == "passed", f"Export comparison failed: {label}")
        # Publish analytical results only, without execution/workflow envelopes.
        results.append({"analysis": label, "status": "passed", "expected": expected,
                        "observed": observed,
                        "field_comparisons": result.get("field_comparisons", result.get("numeric_comparisons_total")),
                        "null_field_comparisons": result.get("null_field_comparisons", result.get("expected_null_cells", 0))})
    return results


def check_presentation():
    _, monthly = read_csv(BASELINE)
    _, identified = read_csv("reports/bigquery_identified_sales_breakdown.csv")
    columns, segments = read_csv("reports/11_customer_segments.csv")
    _, cohorts = read_csv("reports/bigquery_cohort_purchase_timing.csv")
    periods = period_summary(monthly, identified)
    base, sensitivity = segment_partitions(segments)
    cohort_checks(cohorts)
    outputs = [("exports/tableau_sales_periods.csv", PERIOD_COLUMNS, periods),
               ("exports/tableau_customer_segments_base.csv", columns, base),
               ("exports/tableau_customer_segments_sensitivity.csv", columns, sensitivity)]
    for path, fields, rows in outputs:
        require(Path(path).read_bytes() == csv_bytes(fields, rows), f"Presentation output differs: {path}")
    return {"sales_period_rows": 2, "base_segment_rows": 5, "sensitivity_rows": 30,
            "cohort_rows": len(cohorts), "status": "passed"}


def text(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def compare_rows(rows, expected_path, keys):
    columns, expected_rows = read_csv(expected_path)
    actual = [{field: text(row[field]) for field in columns} for row in rows]
    index = lambda records: {tuple(row[k] for k in keys): row for row in records}
    expected, observed = index(expected_rows), index(actual)
    require(len(expected) == len(expected_rows) == len(observed) == len(actual), f"Duplicate/count discrepancy: {expected_path}")
    require(set(expected) == set(observed), f"Rebuilt key discrepancy: {expected_path}")
    matches = 0
    for key, row in observed.items():
        for field in columns:
            left, right = row[field], expected[key][field]
            if field in keys:
                equal = left == right
            elif left == "" or right == "":
                equal = left == right
            elif left.lower() in {"true", "false"} or right.lower() in {"true", "false"}:
                equal = left.lower() == right.lower()
            else:
                try:
                    equal = Decimal(left).is_finite() and Decimal(left) == Decimal(right)
                except InvalidOperation:
                    equal = left == right
            require(equal, f"Source reconstruction differs: {expected_path}, {key}, {field}")
            matches += 1
    return {"expected": expected_path, "rows": len(actual), "cells_including_keys": matches, "status": "passed"}


def check_from_source():
    from monthly_sales_baseline import aggregate as monthly, raw_rows
    from identified_sales_breakdown import aggregate as identified
    from recorded_credit_analysis import extract_credits, build_outputs
    from first_observed_purchases import rebuild_purchase_inputs, build_customer_histories
    from cohort_purchase_timing import aggregate as cohorts
    from customer_snapshot import aggregate as snapshot, FEATURE_COLUMNS, FEATURE_CUTOFF
    from customer_segments import aggregate as segments, load_rules

    manifest = json.loads(Path("reports/load_manifest.json").read_text(encoding="utf-8"))
    rules, marker = load_code_rules(), manifest["null_marker"]
    checks = []
    with localcontext() as context:
        context.prec = 80
        monthly_rows, _, _ = monthly(raw_rows(manifest), rules, marker)
        checks.append(compare_rows(monthly_rows, "reports/monthly_sales_expected.csv", ["month_start"]))
        identified_rows, _ = identified(raw_rows(manifest), rules, marker)
        checks.append(compare_rows(identified_rows, "reports/identified_sales_expected.csv", ["period"]))
        credit_lines, _ = extract_credits(raw_rows(manifest), rules, marker)
        credit_rows, _, _ = build_outputs(credit_lines)
        checks.append(compare_rows(credit_rows, "reports/recorded_credit_expected.csv", ["section", "period", "detail_key"]))
        invoices, controls = rebuild_purchase_inputs(raw_rows(manifest), rules, marker)
        require(controls["raw_rows"] == manifest["raw"]["rows"] and controls["canonical_rows"] == manifest["canonical"]["rows"], "Source population differs")
        histories, _ = build_customer_histories(invoices)
        checks.append(compare_rows(histories, "reports/first_observed_purchases_expected.csv", ["customer_id"]))
        string_histories = [{k: text(v) for k, v in row.items()} for row in histories]
        checks.append(compare_rows(cohorts(string_histories), "reports/cohort_purchase_timing_expected.csv", ["cohort_month"]))
        customers = snapshot(invoices)
        checks.append(compare_rows(customers, "reports/customer_snapshot_expected.csv", ["customer_id"]))
        historical = snapshot([i for i in invoices if i["invoice_date"] <= FEATURE_CUTOFF])
        require([[r[f] for f in FEATURE_COLUMNS] for r in customers] == [[r[f] for f in FEATURE_COLUMNS] for r in historical], "Future records affected snapshot features")
        string_customers = [{k: text(v) for k, v in row.items()} for row in customers]
        grouped, _, _ = segments(string_customers, load_rules())
        checks.append(compare_rows(grouped, "reports/customer_segments_expected.csv", ["scenario_id", "segment_id"]))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-source", action="store_true", help="Also reconstruct expected analyses from the six raw CSVs without writing them")
    parser.add_argument("--output", type=Path, help="Optional validation summary; cannot overwrite an input")
    args = parser.parse_args()
    files = check_input_hashes()
    if args.output:
        require(args.output.resolve() not in {Path(p).resolve() for p in files} | {MANIFEST.resolve()}, "Output cannot overwrite analytical inputs")
    results = {"status": "passed", "input_hashes": len(files), "export_comparisons": compare_exports(), "presentation": check_presentation()}
    if args.from_source:
        results["source_reconstruction"] = check_from_source()
    check_input_hashes()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"PASSED: {len(files)} unchanged public inputs, 8 export comparisons, 3 exact presentation outputs" + (", 7 source reconstructions" if args.from_source else "") + ".")


if __name__ == "__main__":
    main()
