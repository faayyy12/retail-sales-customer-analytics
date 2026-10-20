"""Compare an unchanged BigQuery category-control CSV with local expected controls."""

import argparse
from collections import Counter
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo


KEY_COLUMNS = ["period", "item_category", "line_status"]
COUNT_COLUMNS = ["rows", "invoices", "identified_customers", "missing_customer_id_rows"]
MONEY_COLUMNS = ["signed_amount_gbp", "gross_merchandise_amount", "recorded_merchandise_credit_amount", "identified_gross_merchandise_amount"]
COLUMNS = KEY_COLUMNS + COUNT_COLUMNS + MONEY_COLUMNS


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_controls(path):
    before = sha256(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        rows = list(reader)
    keys = [tuple(row.get(field) for field in KEY_COLUMNS) for row in rows]
    info = {
        "path": path.as_posix(), "bytes": path.stat().st_size,
        "sha256_before": before, "headers": headers,
        "column_count": len(headers), "data_rows": len(rows),
        "schema_matches": len(headers) == len(COLUMNS) and len(set(headers)) == len(COLUMNS) and set(headers) == set(COLUMNS),
        "duplicate_keys": [{"key": list(key), "records": count} for key, count in Counter(keys).items() if count > 1],
        "invalid_keys": [{"data_record": i + 1, "key": list(key)} for i, key in enumerate(keys) if any(value is None or value == "" for value in key)],
        "malformed_csv_record_numbers": [i + 2 for i, row in enumerate(rows) if None in row or any(value is None for value in row.values())],
    }
    return info, rows


def compare(expected_path, observed_path, checked_on):
    expected_info, expected_rows = read_controls(expected_path)
    observed_info, observed_rows = read_controls(observed_path)
    structural_errors = []
    for label, info in [("expected", expected_info), ("observed", observed_info)]:
        if not info["schema_matches"] or info["data_rows"] != 63 or info["duplicate_keys"] or info["invalid_keys"] or info["malformed_csv_record_numbers"]:
            structural_errors.append({"file": label, "structural_failure": True})
    expected = {tuple(row.get(field) for field in KEY_COLUMNS): row for row in expected_rows}
    observed = {tuple(row.get(field) for field in KEY_COLUMNS): row for row in observed_rows}
    missing = sorted(set(expected) - set(observed), key=str)
    extra = sorted(set(observed) - set(expected), key=str)
    mismatches = []
    counts = {"integer_count_fields": 0, "monetary_fields": 0}
    with localcontext() as context:
        context.prec = 60
        if not structural_errors:
            for key in sorted(set(expected) & set(observed)):
                for field in COUNT_COLUMNS + MONEY_COLUMNS:
                    left, right = expected[key][field], observed[key][field]
                    kind = "integer_count_fields" if field in COUNT_COLUMNS else "monetary_fields"
                    counts[kind] += 1
                    detail = {"key": list(key), "field": field, "expected": left, "observed": right}
                    try:
                        left_value, right_value = Decimal(left), Decimal(right)
                        if not left_value.is_finite() or not right_value.is_finite():
                            raise ValueError("Nonfinite numeric value")
                        if field in COUNT_COLUMNS and any(value < 0 or value != value.to_integral_value() for value in (left_value, right_value)):
                            raise ValueError("Counts must be nonnegative integers")
                        if left_value != right_value:
                            mismatches.append({**detail, "difference": str(right_value - left_value)})
                    except (InvalidOperation, ValueError) as error:
                        mismatches.append({**detail, "error": str(error)})
        for info, path in [(expected_info, expected_path), (observed_info, observed_path)]:
            info["sha256_after"] = sha256(path)
            info["unchanged"] = info["sha256_before"] == info["sha256_after"]
        unchanged = expected_info["unchanged"] and observed_info["unchanged"]
        passed = not structural_errors and not missing and not extra and not mismatches and unchanged and counts == {"integer_count_fields": 252, "monetary_fields": 252}
        totals = {}
        if passed:
            for label, rows in [("expected", expected_rows), ("observed", observed_rows)]:
                totals[label] = {"rows": sum(int(Decimal(row["rows"])) for row in rows), **{field: str(sum((Decimal(row[field]) for row in rows), Decimal(0))) for field in MONEY_COLUMNS}}
    return {
        "checked_on": checked_on, "status": "passed" if passed else "failed",
        "scope": "Local comparison only; no cloud access. Observed CSV provenance supplied by owner.",
        "expected": expected_info, "observed": observed_info,
        "observed_filename_note": "Double .csv extension preserved." if observed_path.name.endswith(".csv.csv") else "Observed filename preserved.",
        "required_schema": COLUMNS, "key_columns": KEY_COLUMNS,
        "row_order_ignored": True, "column_order_matches": expected_info["headers"] == observed_info["headers"],
        "count_columns": COUNT_COLUMNS, "money_columns": MONEY_COLUMNS,
        "numeric_method": "Decimal from CSV text; finite values only; count values must be nonnegative integers; exact equality without tolerance or rounding. Trailing zeros are numerically equal. Precision 60 for supplemental sums. No floating point.",
        "structural_errors": structural_errors,
        "missing_keys": [list(key) for key in missing], "extra_keys": [list(key) for key in extra],
        "comparisons": counts, "numeric_comparisons_total": sum(counts.values()),
        "mismatches": mismatches, "both_input_files_unchanged": unchanged,
        "supplemental_additive_totals": totals,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, default=Path("reports/cleaning_category_controls.csv"))
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/cleaning_category_comparison.json"))
    parser.add_argument("--checked-on", default=datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat())
    args = parser.parse_args()
    if args.output.resolve() in {args.expected.resolve(), args.observed.resolve()}:
        parser.error("Comparison output must not overwrite either input CSV.")
    result = compare(args.expected, args.observed, args.checked_on)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{result['status'].upper()}: {result['numeric_comparisons_total']} numeric comparisons; {len(result['mismatches'])} field mismatches. Evidence: {args.output}")
    if result["status"] != "passed":
        print(json.dumps({key: result[key] for key in ("structural_errors", "missing_keys", "extra_keys", "mismatches")}, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
