"""Exact local comparison of the owner's identified-sales BigQuery export."""

import argparse
from collections import Counter
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from compare_category_controls import sha256
from identified_sales_breakdown import (COLUMNS, COUNT_COLUMNS, MONEY_COLUMNS, PERIODS,
                                       RATIO_COLUMNS, baseline_controls, exact_identity)
from monthly_sales_baseline import ratio


def read_controls(path):
    before = sha256(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers, rows = reader.fieldnames or [], list(reader)
    keys = [row.get("period") for row in rows]
    info = {"path": path.as_posix(), "bytes": path.stat().st_size, "sha256_before": before,
            "data_rows": len(rows), "column_count": len(headers),
            "schema_matches": len(headers) == len(COLUMNS) and set(headers) == set(COLUMNS),
            "duplicate_periods": [key for key, count in Counter(keys).items() if count > 1],
            "missing_periods": sorted(set(PERIODS) - set(keys)),
            "extra_periods": sorted(set(keys) - set(PERIODS), key=str),
            "malformed_records": [index + 2 for index, row in enumerate(rows)
                                  if None in row or any(value is None for value in row.values())]}
    return info, {row.get("period"): row for row in rows}


def numeric(text, field):
    if text == "" and field in RATIO_COLUMNS:
        return None
    value = Decimal(text)
    if not value.is_finite():
        raise ValueError("Nonfinite value")
    if field in COUNT_COLUMNS and (value < 0 or value != value.to_integral_value()):
        raise ValueError("Count must be a nonnegative integer")
    return value


def row_checks(row, baseline):
    values = {field: numeric(row[field], field) for field in COUNT_COLUMNS + MONEY_COLUMNS + RATIO_COLUMNS}
    customers, identified = values["identified_purchasing_customers"], values["identified_purchase_invoice_count"]
    anonymous, total = values["anonymous_purchase_invoice_count"], values["total_purchase_invoice_count"]
    gross = values["identified_gross_merchandise_sales_gbp"]
    anonymous_gross, total_gross = values["anonymous_gross_merchandise_sales_gbp"], values["total_gross_merchandise_sales_gbp"]
    checks = {
        "customer_population": 0 <= customers <= identified and bool(customers) == bool(identified),
        "invoice_split": identified + anonymous == total,
        "money_split": gross + anonymous_gross == total_gross,
        "nonnegative_money": min(gross, anonymous_gross, total_gross) >= 0,
        "purchase_money_population": bool(gross) == bool(identified) and bool(anonymous_gross) == bool(anonymous),
        "purchases_per_customer": values["purchases_per_identified_customer"] == ratio(identified, customers),
        "identified_invoice_value": values["average_identified_purchase_invoice_value_gbp"] == ratio(gross, identified),
        "identified_share": values["identified_gross_sales_share"] == ratio(gross, total_gross),
    }
    for field, expected in baseline.items():
        checks[f"verified_baseline.{field}"] = values[field] == expected
    identity = exact_identity(row)
    if identity["defined"]:
        checks["exact_unrounded_identity"] = identity["identity_matches"]
    return [name for name, passed in checks.items() if not passed], identity


def compare(expected_path, observed_path, baseline_path):
    baseline_before = sha256(baseline_path)
    expected_info, expected = read_controls(expected_path)
    observed_info, observed = read_controls(observed_path)
    errors, mismatches, control_errors, identities = [], [], [], []
    for label, info in (("expected", expected_info), ("observed", observed_info)):
        if (not info["schema_matches"] or info["data_rows"] != 2 or info["duplicate_periods"]
                or info["missing_periods"] or info["extra_periods"] or info["malformed_records"]):
            errors.append(label)
    counts = {"count_fields": 0, "money_fields": 0, "ratio_fields": 0}
    with localcontext() as context:
        context.prec = 80
        baseline = baseline_controls(baseline_path)
        if not errors:
            for period in PERIODS:
                for fields, label in ((COUNT_COLUMNS, "count_fields"), (MONEY_COLUMNS, "money_fields"), (RATIO_COLUMNS, "ratio_fields")):
                    for field in fields:
                        counts[label] += 1
                        detail = {"period": period, "field": field,
                                  "expected": expected[period][field], "observed": observed[period][field]}
                        try:
                            if numeric(detail["expected"], field) != numeric(detail["observed"], field):
                                mismatches.append(detail)
                        except (InvalidOperation, ValueError) as error:
                            mismatches.append({**detail, "error": str(error)})
                for label, rows in (("expected", expected), ("observed", observed)):
                    try:
                        failed, identity = row_checks(rows[period], baseline[period])
                        identities.append({"file": label, "period": period, **identity})
                    except (InvalidOperation, ValueError, ZeroDivisionError) as error:
                        failed = [str(error)]
                    if failed:
                        control_errors.append({"file": label, "period": period, "failed_checks": failed})
    for path, info in ((expected_path, expected_info), (observed_path, observed_info)):
        info["sha256_after"] = sha256(path)
        info["unchanged"] = info["sha256_before"] == info["sha256_after"]
    baseline_after = sha256(baseline_path)
    unchanged = expected_info["unchanged"] and observed_info["unchanged"] and baseline_before == baseline_after
    passed = not errors and not mismatches and not control_errors and unchanged and sum(counts.values()) == 20
    return {"checked_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
            "status": "passed" if passed else "failed", "scope": "Local saved-file comparison only; no cloud access.",
            "expected": expected_info, "observed": observed_info,
            "baseline": {"path": baseline_path.as_posix(), "sha256_before": baseline_before,
                         "sha256_after": baseline_after, "unchanged": baseline_before == baseline_after},
            "structural_errors": errors, "field_mismatches": mismatches, "control_errors": control_errors,
            "exact_identity_checks": identities, "numeric_comparisons": counts,
            "numeric_comparisons_total": sum(counts.values()), "all_inputs_unchanged": unchanged,
            "row_order_ignored": True, "required_schema": COLUMNS,
            "arithmetic": "Decimal exact equality for values; division exports use 9-decimal precision. Identity uses exact Fraction counts/amounts, not rounded export ratios. Twelve period controls reconcile to the monthly baseline.",
            "milestone_3": "In progress; no automatic completion."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, default=Path("reports/identified_sales_expected.csv"))
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/identified_sales_comparison.json"))
    args = parser.parse_args()
    controls = json.loads(Path("reports/identified_sales_local_controls.json").read_text(encoding="utf-8"))
    comparison_path = Path("reports/monthly_sales_comparison.json")
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    baseline_path = Path(comparison["observed"]["path"])
    protected = (args.expected, args.observed, baseline_path, comparison_path,
                 Path("reports/identified_sales_local_controls.json"), Path(comparison["expected"]["path"]))
    if args.output.resolve() in {path.resolve() for path in protected}:
        parser.error("Evidence output must not overwrite any source or control input.")
    if comparison["status"] != "passed" or comparison["numeric_comparisons_total"] != 220:
        parser.error("Monthly baseline comparison must have passed all 220 values.")
    if sha256(args.expected) != controls["expected_sha256"]:
        parser.error("Expected controls changed since preparation; inspect rather than overwrite.")
    if sha256(comparison_path) != controls["input_sha256_before_and_after"][comparison_path.as_posix()]:
        parser.error("Monthly comparison evidence changed since control preparation.")
    for label in ("expected", "observed"):
        info = comparison[label]
        if sha256(Path(info["path"])) != info["sha256_after"]:
            parser.error(f"Verified monthly {label} input changed.")
    result = compare(args.expected, args.observed, baseline_path)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{result['status'].upper()}: {result['numeric_comparisons_total']} numeric comparisons. Evidence: {args.output}")
    if result["status"] != "passed":
        print(json.dumps({field: result[field] for field in ("structural_errors", "field_mismatches", "control_errors")}, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
