"""Compare a manually exported monthly BigQuery CSV with independent local controls."""

import argparse
from collections import Counter
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from compare_category_controls import sha256
from monthly_sales_baseline import COLUMNS, COUNT_COLUMNS, MONEY_COLUMNS, MONTHS, RATIO_COLUMNS, ratio


def read_csv(path):
    before = sha256(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        rows = list(reader)
    keys = [row.get("month_start") for row in rows]
    info = {
        "path": path.as_posix(), "sha256_before": before, "bytes": path.stat().st_size,
        "column_count": len(headers), "data_rows": len(rows),
        "schema_matches": len(headers) == len(COLUMNS) and set(headers) == set(COLUMNS),
        "duplicate_months": [key for key, count in Counter(keys).items() if count > 1],
        "missing_months": sorted(set(MONTHS) - set(keys)),
        "extra_months": sorted(set(keys) - set(MONTHS), key=str),
        "malformed_records": [index + 2 for index, row in enumerate(rows)
                              if None in row or any(value is None for value in row.values())],
    }
    return info, {row.get("month_start"): row for row in rows}


def numeric(text, field):
    if text == "" and field in RATIO_COLUMNS:
        return None
    value = Decimal(text)
    if not value.is_finite():
        raise ValueError("Nonfinite value")
    if field in COUNT_COLUMNS and (value < 0 or value != value.to_integral_value()):
        raise ValueError("Count must be a nonnegative integer")
    return value


def row_errors(row):
    """Algebraic controls supplement per-field comparison, never replace it."""
    values = {field: numeric(row[field], field) for field in COUNT_COLUMNS + MONEY_COLUMNS + RATIO_COLUMNS}
    gross, credits, net, identified = (values[field] for field in MONEY_COLUMNS)
    invoices = values["purchase_invoice_count"]
    checks = {
        "net_equals_gross_minus_credits": net == gross - credits,
        "invoice_split": invoices == values["identified_purchase_invoice_count"] + values["anonymous_purchase_invoice_count"],
        "customer_count_population": 0 <= values["identified_purchasing_customers"] <= values["identified_purchase_invoice_count"],
        "monetary_population": gross >= 0 and credits >= 0 and 0 <= identified <= gross,
        "average_invoice_value": values["average_purchase_invoice_value_gbp"] == ratio(gross, invoices),
        "identified_share": values["identified_gross_sales_share"] == ratio(identified, gross),
    }
    return [name for name, passed in checks.items() if not passed]


def compare(expected_path, observed_path):
    expected_info, expected = read_csv(expected_path)
    observed_info, observed = read_csv(observed_path)
    structural_errors, mismatches, identity_errors = [], [], []
    for label, info in (("expected", expected_info), ("observed", observed_info)):
        if (not info["schema_matches"] or info["data_rows"] != 22 or info["duplicate_months"]
                or info["missing_months"] or info["extra_months"] or info["malformed_records"]):
            structural_errors.append(label)
    counts = {"count_fields": 0, "money_fields": 0, "ratio_fields": 0}
    with localcontext() as context:
        context.prec = 80
        if not structural_errors:
            for month in MONTHS:
                for fields, label in ((COUNT_COLUMNS, "count_fields"), (MONEY_COLUMNS, "money_fields"), (RATIO_COLUMNS, "ratio_fields")):
                    for field in fields:
                        counts[label] += 1
                        detail = {"month_start": month, "field": field,
                                  "expected": expected[month][field], "observed": observed[month][field]}
                        try:
                            left = numeric(detail["expected"], field)
                            right = numeric(detail["observed"], field)
                            if left != right:
                                mismatches.append(detail)
                        except (InvalidOperation, ValueError) as error:
                            mismatches.append({**detail, "error": str(error)})
                for label, rows in (("expected", expected), ("observed", observed)):
                    try:
                        errors = row_errors(rows[month])
                    except (InvalidOperation, ValueError) as error:
                        errors = [str(error)]
                    if errors:
                        identity_errors.append({"file": label, "month_start": month, "failed_checks": errors})
    for path, info in ((expected_path, expected_info), (observed_path, observed_info)):
        info["sha256_after"] = sha256(path)
        info["unchanged"] = info["sha256_before"] == info["sha256_after"]
    unchanged = expected_info["unchanged"] and observed_info["unchanged"]
    passed = (not structural_errors and not mismatches and not identity_errors and unchanged
              and counts == {"count_fields": 88, "money_fields": 88, "ratio_fields": 44})
    return {
        "checked_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
        "status": "passed" if passed else "failed", "scope": "Local saved-file comparison; no cloud access.",
        "expected": expected_info, "observed": observed_info,
        "row_order_ignored": True, "required_schema": COLUMNS,
        "structural_errors": structural_errors, "field_mismatches": mismatches,
        "identity_errors": identity_errors, "numeric_comparisons": counts,
        "numeric_comparisons_total": sum(counts.values()), "both_inputs_unchanged": unchanged,
        "arithmetic": "Decimal exact equality, no tolerance; four money totals unrounded; two ratios explicitly 9 decimals, half away from zero.",
        "milestone_3": "Not automatically completed; remaining sales analysis still required.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, default=Path("reports/monthly_sales_expected.csv"))
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/monthly_sales_comparison.json"))
    args = parser.parse_args()
    if args.output.resolve() in (args.expected.resolve(), args.observed.resolve()):
        parser.error("Evidence output must not overwrite either CSV input.")
    result = compare(args.expected, args.observed)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{result['status'].upper()}: {result['numeric_comparisons_total']} numeric comparisons; evidence: {args.output}")
    if result["status"] != "passed":
        print(json.dumps({field: result[field] for field in ("structural_errors", "field_mismatches", "identity_errors")}, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
