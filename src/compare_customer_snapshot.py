"""Exact customer snapshot CSV comparison, with feature/outcome integrity checks."""

import argparse
from collections import Counter
import csv
from datetime import date, datetime
from decimal import InvalidOperation, localcontext
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cohort_purchase_timing import check_hashes
from compare_category_controls import sha256
from customer_snapshot import COLUMNS, COUNT_COLUMNS, row_errors, summary_controls, typed


def read_csv(path):
    before = sha256(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        rows = list(reader)
    keys = [row.get("customer_id") for row in rows]
    info = {"path": path.as_posix(), "sha256_before": before, "bytes": path.stat().st_size,
            "data_rows": len(rows), "column_count": len(headers),
            "schema_matches": len(headers) == len(COLUMNS) and set(headers) == set(COLUMNS),
            "duplicate_customer_ids": [key for key, count in Counter(keys).items() if count > 1],
            "invalid_customer_key_rows": [index + 2 for index, key in enumerate(keys) if not key or key != key.strip()],
            "malformed_records": [index + 2 for index, row in enumerate(rows)
                                  if None in row or any(value is None for value in row.values())]}
    return info, {row.get("customer_id"): row for row in rows}


def compare(expected_path, observed_path):
    expected_info, expected = read_csv(expected_path)
    observed_info, observed = read_csv(observed_path)
    structural, mismatches, semantic = [], [], []
    for label, info in (("expected", expected_info), ("observed", observed_info)):
        if (not info["schema_matches"] or info["duplicate_customer_ids"] or info["invalid_customer_key_rows"]
                or info["malformed_records"]):
            structural.append(label)
    missing, extra = sorted(set(expected) - set(observed), key=str), sorted(set(observed) - set(expected), key=str)
    if missing or extra or expected_info["data_rows"] != observed_info["data_rows"]:
        structural.append("customer_population")
    compared, controls = 0, {}
    with localcontext() as context:
        context.prec = 80
        if not structural:
            normalized = {"expected": [], "observed": []}
            for customer in sorted(expected):
                parsed = {}
                for label, rows in (("expected", expected), ("observed", observed)):
                    parsed[label] = {}
                    for field in COLUMNS[1:]:
                        try:
                            parsed[label][field] = typed(rows[customer][field], field)
                        except (ValueError, TypeError, InvalidOperation) as error:
                            semantic.append({"file": label, "customer_id": customer, "field": field, "error": str(error)})
                    if len(parsed[label]) == len(COLUMNS) - 1:
                        errors = row_errors(parsed[label])
                        if errors:
                            semantic.append({"file": label, "customer_id": customer, "failed_checks": errors})
                        normalized[label].append({"customer_id": customer, **{
                            field: str(value).lower() if isinstance(value, bool) else value.isoformat()
                            if isinstance(value, date) else str(int(value)) if field in COUNT_COLUMNS
                            else str(value) for field, value in parsed[label].items()}})
                for field in COLUMNS[1:]:
                    compared += 1
                    if field in parsed["expected"] and field in parsed["observed"]:
                        if parsed["expected"][field] != parsed["observed"][field]:
                            mismatches.append({"customer_id": customer, "field": field,
                                               "expected": expected[customer][field], "observed": observed[customer][field]})
            for label, rows in normalized.items():
                if len(rows) == len(expected):
                    controls[label] = summary_controls(rows)
    for path, info in ((expected_path, expected_info), (observed_path, observed_info)):
        info["sha256_after"] = sha256(path)
        info["unchanged"] = info["sha256_before"] == info["sha256_after"]
    passed = (not structural and not mismatches and not semantic
              and expected_info["unchanged"] and observed_info["unchanged"]
              and controls.get("expected") == controls.get("observed")
              and compared == len(expected) * (len(COLUMNS) - 1))
    return {"status": "passed" if passed else "failed", "expected": expected_info, "observed": observed_info,
            "missing_customer_ids": missing, "extra_customer_ids": extra, "structural_errors": structural,
            "field_comparisons": compared, "field_mismatches": mismatches, "semantic_errors": semantic,
            "summary_controls": controls, "null_fields_allowed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observed", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/customer_snapshot_comparison.json"))
    args = parser.parse_args()
    evidence_path = Path("reports/customer_snapshot_local_controls.json")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    expected = Path(evidence["output_path"])
    inputs = dict(evidence["input_sha256_before_and_after"])
    inputs[expected.as_posix()] = evidence["output_sha256"]
    inputs[evidence_path.as_posix()] = sha256(evidence_path)
    protected = {Path(name).resolve() for name in inputs}
    if args.observed.resolve() in protected or args.output.resolve() in protected | {args.observed.resolve()}:
        raise ValueError("Observed CSV must be separate; comparison output cannot overwrite inputs or evidence")
    check_hashes(inputs)
    result = compare(expected, args.observed)
    controls_ok = result["summary_controls"].get("expected") == evidence["summary_controls"]
    if (not controls_ok or result["expected"]["data_rows"] != evidence["expected_rows"]
            or result["field_comparisons"] != evidence["expected_nonnull_field_cells"]):
        result["status"] = "failed"
    result["preparation_controls_match"] = controls_ok
    check_hashes(inputs)
    result.update({"compared_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                   "input_sha256_before_and_after": inputs,
                   "execution": "Local comparison of owner-supplied customer snapshot export; no cloud query or dry run.",
                   "arithmetic": "Exact Decimal equality for counts and unrounded monetary amounts; exact dates/boolean, no NULLs, tolerance or row-order dependence.",
                   "cloud_job_metadata": "Unconfirmed unless separately supplied by owner.",
                   "milestone_5": "in progress; segment rules and their subsequent evaluation remain pending"})
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{result['status'].upper()}: {result['observed']['data_rows']} customers; {result['field_comparisons']} fields; "
          f"{len(result['field_mismatches'])} mismatches; {len(result['semantic_errors'])} semantic errors. Evidence: {args.output}")
    if result["status"] != "passed":
        print(json.dumps({key: result[key] for key in ("structural_errors", "missing_customer_ids", "extra_customer_ids",
                                                     "field_mismatches", "semantic_errors", "preparation_controls_match")}, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
