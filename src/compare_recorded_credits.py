"""Compare the manual recorded-credit export to independent local controls."""

import argparse
from collections import Counter
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from compare_category_controls import sha256
from identified_sales_breakdown import PERIODS
from monthly_sales_baseline import MONTHS, ratio
from recorded_credit_analysis import (COLUMNS, COUNT_COLUMNS, KEY_COLUMNS,
                                      MONEY_COLUMNS, RATIO_COLUMNS, SECTION_ORDER)


def read_export(path):
    before = sha256(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers, rows = reader.fieldnames or [], list(reader)
    keys = [tuple(row.get(field) for field in KEY_COLUMNS) for row in rows]
    info = {"path": path.as_posix(), "bytes": path.stat().st_size, "sha256_before": before,
            "data_rows": len(rows), "column_count": len(headers),
            "schema_matches": len(headers) == len(COLUMNS) and set(headers) == set(COLUMNS),
            "duplicate_keys": [list(key) for key, count in Counter(keys).items() if count > 1],
            "invalid_keys": [list(key) for key in keys if key[0] not in SECTION_ORDER or key[1] not in PERIODS or not key[2]],
            "malformed_records": [index + 2 for index, row in enumerate(rows)
                                  if None in row or any(value is None for value in row.values())]}
    return info, {key: row for key, row in zip(keys, rows)}


def number(text, field):
    if text == "":
        return None
    value = Decimal(text)
    if not value.is_finite():
        raise ValueError("Nonfinite value")
    if value < 0:
        raise ValueError("Credit magnitudes, ratios and counts must be nonnegative")
    if field in COUNT_COLUMNS and value != value.to_integral_value():
        raise ValueError("Counts/ranks must be integers")
    return value


def check_populations(rows, baseline_totals):
    errors = []
    parsed = {}
    for key, row in rows.items():
        values = {field: number(row[field], field) for field in COUNT_COLUMNS + MONEY_COLUMNS + RATIO_COLUMNS}
        parsed[key] = values
        amount, count = values["recorded_credits_gbp"], values["credit_invoice_count"]
        if amount is None or values["credit_line_count"] is None or values["row_rank"] is None:
            raise ValueError(f"Required numeric cell is NULL: {key}")
        if key[0] == "stock_change":
            if count is not None or values["average_credit_per_invoice_gbp"] is not None:
                errors.append({"key": list(key), "check": "stock invoice counts/means must be NULL"})
        else:
            if count is None or values["credit_line_count"] < count:
                errors.append({"key": list(key), "check": "invalid invoice/line population"})
            elif values["average_credit_per_invoice_gbp"] != ratio(amount, count):
                errors.append({"key": list(key), "check": "mean does not equal amount/invoices"})
        if values["share_of_period_credits"] != ratio(amount, baseline_totals[key[1]]):
            errors.append({"key": list(key), "check": "share differs from verified baseline"})
    for period in PERIODS:
        summary = parsed.get(("period_summary", period, "__ALL__"))
        if summary is None:
            errors.append({"period": period, "check": "missing period summary"})
            continue
        if summary["recorded_credits_gbp"] != baseline_totals[period]:
            errors.append({"period": period, "check": "period total differs from verified baseline"})
        for section in SECTION_ORDER:
            selected = [value for key, value in parsed.items() if key[0] == section and key[1] == period]
            fields = MONEY_COLUMNS + ["credit_line_count"]
            if section != "stock_change":
                fields += ["credit_invoice_count"]
            for field in fields:
                total = sum((value[field] for value in selected), Decimal(0))
                if total != summary[field]:
                    errors.append({"period": period, "section": section, "field": field,
                                   "check": "display plus remainder does not reconcile"})
    return errors


def compare(expected_path, observed_path, baseline_path):
    baseline_before = sha256(baseline_path)
    with baseline_path.open(encoding="utf-8-sig", newline="") as stream:
        baseline_rows = list(csv.DictReader(stream))
    if len(baseline_rows) != 22 or {row["month_start"] for row in baseline_rows} != set(MONTHS):
        raise ValueError("Invalid baseline month keys")
    expected_info, expected = read_export(expected_path)
    observed_info, observed = read_export(observed_path)
    structural, mismatches, population_errors = [], [], []
    for label, info in (("expected", expected_info), ("observed", observed_info)):
        if not info["schema_matches"] or info["duplicate_keys"] or info["invalid_keys"] or info["malformed_records"]:
            structural.append(label)
    missing = sorted(set(expected) - set(observed))
    extra = sorted(set(observed) - set(expected))
    counts = {"count_fields": 0, "money_fields": 0, "ratio_fields": 0, "null_cells": 0}
    with localcontext() as context:
        context.prec = 80
        totals = {period: sum((Decimal(row["recorded_merchandise_credits_gbp"]) for row in baseline_rows
                              if row["month_start"].startswith(period[:4])), Decimal(0)) for period in PERIODS}
        if not structural:
            for key in sorted(set(expected) & set(observed)):
                for fields, kind in ((COUNT_COLUMNS, "count_fields"), (MONEY_COLUMNS, "money_fields"), (RATIO_COLUMNS, "ratio_fields")):
                    for field in fields:
                        left, right = expected[key][field], observed[key][field]
                        counts["null_cells" if left == "" else kind] += 1
                        detail = {"key": list(key), "field": field, "expected": left, "observed": right}
                        try:
                            if number(left, field) != number(right, field):
                                mismatches.append(detail)
                        except (InvalidOperation, ValueError) as error:
                            mismatches.append({**detail, "error": str(error)})
            for label, rows in (("expected", expected), ("observed", observed)):
                try:
                    for error in check_populations(rows, totals):
                        population_errors.append({"file": label, **error})
                except (InvalidOperation, ValueError, TypeError) as error:
                    population_errors.append({"file": label, "error": str(error)})
    for path, info in ((expected_path, expected_info), (observed_path, observed_info)):
        info["sha256_after"] = sha256(path)
        info["unchanged"] = info["sha256_before"] == info["sha256_after"]
    baseline_after = sha256(baseline_path)
    unchanged = expected_info["unchanged"] and observed_info["unchanged"] and baseline_before == baseline_after
    passed = not structural and not missing and not extra and not mismatches and not population_errors and unchanged
    return {"checked_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
            "status": "passed" if passed else "failed", "scope": "Local CSV comparison only; no cloud access.",
            "expected": expected_info, "observed": observed_info,
            "baseline": {"path": baseline_path.as_posix(), "sha256_before": baseline_before,
                         "sha256_after": baseline_after, "unchanged": baseline_before == baseline_after},
            "structural_errors": structural, "missing_keys": [list(key) for key in missing],
            "extra_keys": [list(key) for key in extra], "field_mismatches": mismatches,
            "population_errors": population_errors, "comparisons": counts,
            "numeric_comparisons_total": sum(counts[field] for field in ("count_fields", "money_fields", "ratio_fields")),
            "all_inputs_unchanged": unchanged, "row_and_column_order_ignored": True,
            "arithmetic": "Exact Decimal equality; only division outputs use defined 9-decimal precision. NULL is distinct from zero. Each section separately reconciles to verified period credits, with remainder totals.",
            "milestone_3": "In progress; no automatic completion."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, default=Path("reports/recorded_credit_expected.csv"))
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/recorded_credit_comparison.json"))
    args = parser.parse_args()
    controls_path = Path("reports/recorded_credit_local_controls.json")
    controls = json.loads(controls_path.read_text(encoding="utf-8"))
    baseline = Path("reports/bigquery_monthly_sales_baseline.csv")
    protected = {args.expected.resolve(), args.observed.resolve(), controls_path.resolve()}
    protected.update(Path(name).resolve() for name in controls["input_sha256_before_and_after"])
    if args.output.resolve() in protected:
        parser.error("Evidence output must not overwrite a CSV, source or control input.")
    if sha256(args.expected) != controls["expected_sha256"]:
        parser.error("Expected CSV changed since preparation; inspect rather than overwrite.")
    for name in ("reports/monthly_sales_comparison.json", "reports/identified_sales_comparison.json", baseline.as_posix()):
        if sha256(Path(name)) != controls["input_sha256_before_and_after"][name]:
            parser.error(f"Verified prerequisite changed: {name}")
    result = compare(args.expected, args.observed, baseline)
    result["expected_numeric_cells"] = controls["expected_numeric_cells"]
    result["expected_null_cells"] = controls["expected_null_cells"]
    if (result["numeric_comparisons_total"] != controls["expected_numeric_cells"]
            or result["comparisons"]["null_cells"] != controls["expected_null_cells"]
            or result["observed"]["data_rows"] != controls["expected_rows"]):
        result["status"] = "failed"
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{result['status'].upper()}: {result['numeric_comparisons_total']} numeric and {result['comparisons']['null_cells']} NULL comparisons. Evidence: {args.output}")
    if result["status"] != "passed":
        print(json.dumps({field: result[field] for field in ("structural_errors", "missing_keys", "extra_keys", "field_mismatches", "population_errors")}, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
