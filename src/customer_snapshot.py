"""Independent snapshot expectations from verified raw inputs; no cloud access."""

from collections import defaultdict
import csv
from datetime import date, datetime
from decimal import Decimal, localcontext
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cleaning_rules import load_code_rules
from cohort_purchase_timing import check_hashes, load_histories
from compare_category_controls import sha256
from first_observed_purchases import build_customer_histories, rebuild_purchase_inputs
from monthly_sales_baseline import raw_rows


FEATURE_CUTOFF = date(2011, 8, 31)
OUTCOME_START = date(2011, 9, 1)
OUTCOME_END = date(2011, 11, 29)
SOURCE_START = date(2009, 12, 1)
DATE_COLUMNS = ["feature_cutoff_date", "first_observed_purchase_date", "last_observed_purchase_date",
                "outcome_start_date", "outcome_end_date"]
COUNT_COLUMNS = ["recency_days", "purchase_invoice_count", "purchase_day_count", "observed_tenure_days",
                 "subsequent_90d_purchase_invoice_count"]
MONEY_COLUMNS = ["gross_merchandise_purchase_amount_gbp", "subsequent_90d_gross_purchase_amount_gbp"]
BOOL_COLUMN = "subsequent_90d_purchase"
COLUMNS = ["customer_id", "feature_cutoff_date", "first_observed_purchase_date", "last_observed_purchase_date",
           "recency_days", "purchase_invoice_count", "purchase_day_count", MONEY_COLUMNS[0], "observed_tenure_days",
           "outcome_start_date", "outcome_end_date", BOOL_COLUMN, "subsequent_90d_purchase_invoice_count", MONEY_COLUMNS[1]]
FEATURE_COLUMNS = ["customer_id", *COLUMNS[1:9]]
ZERO = Decimal(0)


def typed(text, field):
    """No NULL fields in this result; zero outcomes are observed zero, not blanks."""
    if field in DATE_COLUMNS:
        value = date.fromisoformat(text)
        if value.isoformat() != text:
            raise ValueError("Date must use YYYY-MM-DD")
        return value
    if field == BOOL_COLUMN:
        if text.lower() not in ("true", "false"):
            raise ValueError("Outcome must be TRUE/FALSE, never NULL")
        return text.lower() == "true"
    value = Decimal(text)
    if not value.is_finite() or value < 0:
        raise ValueError("Value must be finite and nonnegative")
    if field in COUNT_COLUMNS and value != value.to_integral_value():
        raise ValueError("Count/day value must be an integer")
    return value


def row_errors(values):
    first, last = values["first_observed_purchase_date"], values["last_observed_purchase_date"]
    count, days = values["purchase_invoice_count"], values["purchase_day_count"]
    later_count, later_gross = values["subsequent_90d_purchase_invoice_count"], values[MONEY_COLUMNS[1]]
    checks = {"fixed_feature_cutoff": values["feature_cutoff_date"] == FEATURE_CUTOFF,
              "feature_dates_within_history": SOURCE_START <= first <= last <= FEATURE_CUTOFF,
              "recency_reference_date": values["recency_days"] == (FEATURE_CUTOFF - last).days,
              "observed_tenure_reference_date": values["observed_tenure_days"] == (FEATURE_CUTOFF - first).days,
              "day_invoice_population": 1 <= days <= count and days <= (last - first).days + 1,
              "date_span_day_count": (first == last) == (days == 1),
              "positive_feature_gross": values[MONEY_COLUMNS[0]] > 0,
              "fixed_90_day_outcome_window": values["outcome_start_date"] == OUTCOME_START
                  and values["outcome_end_date"] == OUTCOME_END,
              "outcome_flag_count_amount": values[BOOL_COLUMN] == (later_count > 0)
                  and ((later_count == 0 and later_gross == 0) or (later_count > 0 and later_gross > 0))}
    return [name for name, passed in checks.items() if not passed]


def aggregate(invoices):
    """Two passes freeze historical features before looking at any outcome record."""
    features, outcomes, seen = {}, {}, set()
    for invoice in invoices:
        number, customer, day = invoice["invoice_no"], invoice["customer_id"], invoice["invoice_date"]
        if number in seen:
            raise ValueError(f"Duplicate invoice input: {number}")
        seen.add(number)
        if customer is None or day > FEATURE_CUTOFF:
            continue
        if not isinstance(customer, str) or not customer or customer != customer.strip():
            raise ValueError("Identified customer ID must be an exact nonempty string")
        if day < SOURCE_START or invoice["gross"] <= ZERO:
            raise ValueError("Qualifying invoice has invalid date or gross")
        item = features.setdefault(customer, {"dates": set(), "invoices": set(), "gross": ZERO})
        item["dates"].add(day)
        item["invoices"].add(number)
        item["gross"] += invoice["gross"]
    # Future-only customer IDs cannot add rows or change the frozen feature map.
    for invoice in invoices:
        customer, day = invoice["customer_id"], invoice["invoice_date"]
        if customer not in features or not OUTCOME_START <= day <= OUTCOME_END:
            continue
        if invoice["gross"] <= ZERO:
            raise ValueError("Qualifying outcome invoice must have positive gross")
        item = outcomes.setdefault(customer, {"invoices": set(), "gross": ZERO})
        item["invoices"].add(invoice["invoice_no"])
        item["gross"] += invoice["gross"]
    output = []
    for customer, item in sorted(features.items()):
        first, last = min(item["dates"]), max(item["dates"])
        later = outcomes.get(customer, {"invoices": set(), "gross": ZERO})
        values = {"customer_id": customer, "feature_cutoff_date": FEATURE_CUTOFF.isoformat(),
                  "first_observed_purchase_date": first.isoformat(), "last_observed_purchase_date": last.isoformat(),
                  "recency_days": (FEATURE_CUTOFF - last).days, "purchase_invoice_count": len(item["invoices"]),
                  "purchase_day_count": len(item["dates"]), MONEY_COLUMNS[0]: item["gross"],
                  "observed_tenure_days": (FEATURE_CUTOFF - first).days,
                  "outcome_start_date": OUTCOME_START.isoformat(), "outcome_end_date": OUTCOME_END.isoformat(),
                  BOOL_COLUMN: bool(later["invoices"]), "subsequent_90d_purchase_invoice_count": len(later["invoices"]),
                  MONEY_COLUMNS[1]: later["gross"]}
        row = {field: str(values[field]).lower() if isinstance(values[field], bool) else str(values[field])
               for field in COLUMNS}
        errors = row_errors({field: typed(row[field], field) for field in COLUMNS[1:]})
        if errors:
            raise ValueError(f"Snapshot row semantic errors: {customer}: {errors}")
        output.append(row)
    return output


def summary_controls(rows):
    with localcontext() as context:
        context.prec = 80
        return {"customer_rows": len(rows),
                "historical_purchase_invoices": sum(int(row["purchase_invoice_count"]) for row in rows),
                "historical_customer_purchase_days": sum(int(row["purchase_day_count"]) for row in rows),
                "historical_gross_purchase_amount_gbp": format(sum((Decimal(row[MONEY_COLUMNS[0]]) for row in rows), ZERO).normalize(), "f"),
                "customers_with_subsequent_purchase": sum(row[BOOL_COLUMN].lower() == "true" for row in rows),
                "customers_without_subsequent_purchase": sum(row[BOOL_COLUMN].lower() == "false" for row in rows),
                "subsequent_purchase_invoices": sum(int(row["subsequent_90d_purchase_invoice_count"]) for row in rows),
                "subsequent_gross_purchase_amount_gbp": format(sum((Decimal(row[MONEY_COLUMNS[1]]) for row in rows), ZERO).normalize(), "f"),
                "first_observed_date_min": min((row["first_observed_purchase_date"] for row in rows), default=None),
                "last_observed_date_max": max((row["last_observed_purchase_date"] for row in rows), default=None)}


def reconcile_populations(invoices, rows):
    """Partition main-history identified invoices, including excluded future-only IDs."""
    customers = {row["customer_id"] for row in rows}
    buckets = defaultdict(lambda: {"invoices": 0, "gross": ZERO, "customers": set()})
    for invoice in invoices:
        customer, day = invoice["customer_id"], invoice["invoice_date"]
        if customer is None or day > date(2011, 11, 30):
            continue
        bucket = ("historical_features" if day <= FEATURE_CUTOFF else
                  "november_30_excluded" if day == date(2011, 11, 30) else
                  "outcome_snapshot_customers" if customer in customers else "outcome_future_only_customers")
        item = buckets[bucket]
        item["invoices"] += 1
        item["gross"] += invoice["gross"]
        item["customers"].add(customer)
    if sum(item["invoices"] for item in buckets.values()) != 35818:
        raise ValueError("Identified main-history invoices do not reconcile")
    if sum((item["gross"] for item in buckets.values()), ZERO) != Decimal("16611710.55"):
        raise ValueError("Identified main-history gross does not reconcile")
    controls = summary_controls(rows)
    for key, count, gross in (("historical_features", "historical_purchase_invoices", "historical_gross_purchase_amount_gbp"),
                             ("outcome_snapshot_customers", "subsequent_purchase_invoices", "subsequent_gross_purchase_amount_gbp")):
        if buckets[key]["invoices"] != controls[count] or buckets[key]["gross"] != Decimal(controls[gross]):
            raise ValueError(f"Source-to-customer totals differ: {key}")
    return {key: {"invoices": item["invoices"], "gross_gbp": str(item["gross"]),
                  "distinct_customers": len(item["customers"])} for key, item in sorted(buckets.items())}


def verified_inputs():
    completed_path = Path("reports/milestone_4_completion_review.json")
    completed = json.loads(completed_path.read_text(encoding="utf-8"))
    if completed["status"] != "complete":
        raise ValueError("Customer-history controls must be complete")
    summary = json.loads(Path("reports/repeat_purchasing_summary.json").read_text(encoding="utf-8"))
    inputs = dict(summary["input_sha256_before_and_after"])
    inputs[completed_path.as_posix()] = sha256(completed_path)
    for proof in completed["comparisons"] + [completed["findings_report"], completed["local_descriptive_summary"]]:
        inputs[proof["path"]] = proof["sha256"]
    for name in ("src/customer_snapshot.py", "src/compare_customer_snapshot.py", "sql/10_customer_snapshot.sql",
                 "data/processed/sql/10_customer_snapshot.sql"):
        inputs[name] = sha256(Path(name))
    check_hashes(inputs)
    return inputs


def main():
    inputs = verified_inputs()
    manifest = json.loads(Path("reports/load_manifest.json").read_text(encoding="utf-8"))
    known = json.loads(Path("reports/first_observed_purchases_local_controls.json").read_text(encoding="utf-8"))
    with localcontext() as context:
        context.prec = 80
        invoices, source_checks = rebuild_purchase_inputs(raw_rows(manifest), load_code_rules(), manifest["null_marker"])
        if source_checks != known["source_checks"]:
            raise ValueError("Source counts, invoice consistency or exact canonical amount differ")
        histories, day_checks = build_customer_histories(invoices)
        if histories != sorted(load_histories(Path("reports/first_observed_purchases_expected.csv")), key=lambda row: row["customer_id"]):
            raise ValueError("Rebuilt histories differ from verified customer histories")
        if day_checks != known["customer_day_controls"]:
            raise ValueError("Identified main-history customer/day controls differ")
        rows = aggregate(invoices)
        required_ids = {row["customer_id"] for row in histories if row["first_observed_purchase_date"] <= FEATURE_CUTOFF.isoformat()}
        if {row["customer_id"] for row in rows} != required_ids:
            raise ValueError("Snapshot population differs from verified first-observed histories")
        # Outcomes cannot change historical features: aggregate historical inputs alone.
        historical_rows = aggregate([item for item in invoices if item["invoice_date"] <= FEATURE_CUTOFF])
        if [[row[field] for field in FEATURE_COLUMNS] for row in rows] != [
                [row[field] for field in FEATURE_COLUMNS] for row in historical_rows]:
            raise ValueError("Future records affected historical features")
        partition = reconcile_populations(invoices, rows)
        # Reuse all 22 verified monthly controls for counts, customers and exact gross.
        with Path("reports/monthly_sales_expected.csv").open(encoding="utf-8-sig", newline="") as stream:
            baseline = list(csv.DictReader(stream))
        for month in baseline:
            selected = [item for item in invoices if item["invoice_date"].isoformat()[:7] == month["month_start"][:7]]
            identified = [item for item in selected if item["customer_id"] is not None]
            values = {"purchase_invoice_count": len(selected), "identified_purchase_invoice_count": len(identified),
                      "anonymous_purchase_invoice_count": len(selected) - len(identified),
                      "identified_purchasing_customers": len({item["customer_id"] for item in identified}),
                      "gross_merchandise_sales_gbp": sum((item["gross"] for item in selected), ZERO),
                      "identified_gross_merchandise_sales_gbp": sum((item["gross"] for item in identified), ZERO)}
            for field, value in values.items():
                if Decimal(str(value)) != Decimal(month[field]):
                    raise ValueError(f"Verified monthly control differs: {month['month_start']}: {field}")
    check_hashes(inputs)
    output = Path("reports/customer_snapshot_expected.csv")
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    controls = summary_controls(rows)
    evidence = {"generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                "milestone_5": "in progress; customer dataset only; segments and evaluation pending",
                "cloud_export_comparison": "pending", "execution": "Local source reconstruction and exact controls only; no cloud query or dry run.",
                "output_path": output.as_posix(), "output_sha256": sha256(output), "output_bytes": output.stat().st_size,
                "expected_rows": len(rows), "expected_columns": COLUMNS, "expected_nonnull_field_cells": len(rows) * (len(COLUMNS) - 1),
                "expected_null_field_cells": 0, "input_sha256_before_and_after": inputs, "all_inputs_unchanged": True,
                "feature_cutoff_and_recency_reference_date": FEATURE_CUTOFF.isoformat(),
                "outcome_start_inclusive": OUTCOME_START.isoformat(), "outcome_end_inclusive": OUTCOME_END.isoformat(),
                "outcome_calendar_days": (OUTCOME_END - OUTCOME_START).days + 1,
                "source_checks": source_checks, "main_history_customer_day_checks": day_checks,
                "snapshot_customer_ids_match_verified_first_observed_history": True,
                "features_identical_without_future_records": True, "verified_monthly_controls_matched": len(baseline) * 6,
                "summary_controls": controls, "main_history_identified_invoice_partition": partition,
                "definition": "Identified customers with qualifying purchases on/before 2011-08-31. Features use full earlier history; tenure and recency reference 2011-08-31. Separate subsequent 2011-09-01..2011-11-29 aggregate LEFT JOIN; absent outcomes are FALSE/zero. No future features or segment thresholds.",
                "limitations": "Gross purchases are not profit or net sales; first observed is not acquisition; no subsequent purchase is not proven churn. Anonymous and future-only IDs form no snapshot rows. Later segment rules/evaluation remain pending.",
                "cloud_job_metadata": "Unconfirmed; new snapshot query not executed or dry-run."}
    Path("reports/customer_snapshot_local_controls.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"PASS: {len(rows)} customers x {len(COLUMNS)} columns; source/verified histories/monthly controls reconcile; cloud comparison pending.")
    print(json.dumps(controls, indent=2))
    print(json.dumps(partition, indent=2))


if __name__ == "__main__":
    main()
