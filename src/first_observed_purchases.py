"""Independent customer-day histories and 90-day eligibility; no cloud access."""

import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cleaning_rules import classify, load_code_rules
from compare_category_controls import sha256
from monthly_sales_baseline import raw_rows


CUTOFF = date(2011, 11, 30)
SOURCE_START = date(2009, 12, 1)
DATE_COLUMNS = ["first_observed_purchase_date", "cohort_month", "first_later_day_purchase_date"]
DAY_COLUMN = "days_to_first_later_day_purchase"
BOOL_COLUMNS = ["has_full_90_day_followup", "repeat_purchase_within_90_days"]
COLUMNS = ["customer_id"] + DATE_COLUMNS + [DAY_COLUMN] + BOOL_COLUMNS
ZERO = Decimal(0)


def build_customer_histories(invoices, cutoff=CUTOFF):
    """Use sets/minima independently of SQL windows; all input invoice dates are dates."""
    days = {}
    identified_invoices = 0
    for invoice in invoices:
        customer, purchase_date = invoice["customer_id"], invoice["invoice_date"]
        if customer is None or purchase_date > cutoff:
            continue
        if not isinstance(customer, str) or customer == "":
            raise ValueError("Identified customers must have nonempty string IDs")
        identified_invoices += 1
        days.setdefault(customer, set()).add(purchase_date)
    rows = []
    for customer, observed_days in sorted(days.items()):
        first = min(observed_days)
        later = min((day for day in observed_days if day > first), default=None)
        elapsed = None if later is None else (later - first).days
        eligible = first + timedelta(days=90) <= cutoff
        outcome = None if not eligible else later is not None and 1 <= elapsed <= 90
        rows.append({"customer_id": customer, "first_observed_purchase_date": first.isoformat(),
                     "cohort_month": first.replace(day=1).isoformat(),
                     "first_later_day_purchase_date": "" if later is None else later.isoformat(),
                     DAY_COLUMN: "" if elapsed is None else str(elapsed),
                     "has_full_90_day_followup": str(eligible).lower(),
                     "repeat_purchase_within_90_days": "" if outcome is None else str(outcome).lower()})
    return rows, {"identified_purchase_invoices_through_cutoff": identified_invoices,
                  "unique_customer_purchase_days": sum(len(values) for values in days.values()),
                  "identified_customers_through_cutoff": len(days)}


def rebuild_purchase_inputs(rows, rules, null_marker):
    """Reconcile invoice grain and a separate source-line customer/day population."""
    invoices, direct_customer_days = {}, {}
    raw_count = canonical_count = qualifying_count = 0
    canonical_signed = ZERO
    for row in rows:
        raw_count += 1
        if row["source_sheet"] == "Year 2009-2010" and row["invoice_datetime"] >= "2010-12-01 00:00:00":
            continue
        canonical_count += 1
        quantity, price = int(row["quantity"]), Decimal(row["unit_price"])
        canonical_signed += quantity * price
        description = None if row["description_raw"] == null_marker else row["description_raw"]
        result = classify(row["stock_code_raw"], description, row["description_source_type"],
                          row["invoice_no"], quantity, price, rules)
        if result["line_status"] != "qualifying_purchase":
            continue
        qualifying_count += 1
        customer = None if row["customer_id"] == null_marker else row["customer_id"]
        purchase_date = date.fromisoformat(row["invoice_datetime"][:10])
        if customer is not None and purchase_date <= CUTOFF:
            direct_customer_days.setdefault(customer, set()).add(purchase_date)
        invoice = invoices.setdefault(row["invoice_no"], {"dates": set(), "customers": set(),
                                                         "countries": set(), "gross": ZERO,
                                                         "lines": 0, "anonymous_lines": 0})
        invoice["dates"].add(purchase_date)
        invoice["customers"].add(customer)
        invoice["countries"].add(row["country"])
        invoice["gross"] += result["gross_merchandise_amount"]
        invoice["lines"] += 1
        invoice["anonymous_lines"] += customer is None
    records = []
    for number, invoice in invoices.items():
        if any(len(invoice[field]) != 1 for field in ("dates", "customers", "countries")):
            raise ValueError(f"Conflicting qualifying invoice date/customer/country: {number}")
        records.append({"invoice_no": number, "customer_id": next(iter(invoice["customers"])),
                        "invoice_date": next(iter(invoice["dates"])), "gross": invoice["gross"],
                        "lines": invoice["lines"], "anonymous_lines": invoice["anonymous_lines"]})
    invoice_days = {}
    for invoice in records:
        if invoice["customer_id"] is not None and invoice["invoice_date"] <= CUTOFF:
            invoice_days.setdefault(invoice["customer_id"], set()).add(invoice["invoice_date"])
    if invoice_days != direct_customer_days:
        raise ValueError("Invoice-derived customer/date population differs from qualifying source lines")
    return records, {"raw_rows": raw_count, "canonical_rows": canonical_count,
                     "excluded_overlap_rows": raw_count - canonical_count,
                     "canonical_signed_amount_gbp": str(canonical_signed),
                     "qualifying_lines_all_history": qualifying_count,
                     "qualifying_invoices_all_history": len(records),
                     "invoice_day_customer_country_conflicts": 0,
                     "customer_day_sets_match_qualifying_source_lines": True}


def history_controls(rows):
    eligible = [row for row in rows if row["has_full_90_day_followup"] == "true"]
    ineligible = [row for row in rows if row["has_full_90_day_followup"] == "false"]
    return {"customer_rows": len(rows),
            "eligible_customers": len(eligible), "ineligible_customers": len(ineligible),
            "eligible_true_outcomes": sum(row["repeat_purchase_within_90_days"] == "true" for row in eligible),
            "eligible_false_outcomes": sum(row["repeat_purchase_within_90_days"] == "false" for row in eligible),
            "ineligible_null_outcomes": sum(row["repeat_purchase_within_90_days"] == "" for row in ineligible),
            "ineligible_with_observed_later_day": sum(row["first_later_day_purchase_date"] != "" for row in ineligible),
            "customers_with_observed_later_day": sum(row["first_later_day_purchase_date"] != "" for row in rows),
            "customers_with_only_first_purchase_day": sum(row["first_later_day_purchase_date"] == "" for row in rows),
            "earliest_first_observed_date": min((row["first_observed_purchase_date"] for row in rows), default=None),
            "latest_first_observed_date": max((row["first_observed_purchase_date"] for row in rows), default=None),
            "latest_eligible_first_date": (CUTOFF - timedelta(days=90)).isoformat()}


def main():
    completed_path = Path("reports/milestone_3_completion_review.json")
    completed = json.loads(completed_path.read_text(encoding="utf-8"))
    if completed["status"] != "complete":
        raise ValueError("Sales controls must be complete")
    prior = json.loads(Path("reports/purchase_invoice_sensitivity.json").read_text(encoding="utf-8"))
    inputs = dict(prior["input_sha256_before_and_after"])
    for proof in completed["comparisons"]:
        if sha256(Path(proof["path"])) != proof["sha256"]:
            raise ValueError("Required sales verification evidence changed")
    for proof in (completed["sales_report"],):
        if sha256(Path(proof["path"])) != proof["sha256"]:
            raise ValueError("Completed sales report changed")
    if sha256(Path(prior["output_path"])) != prior["output_sha256"]:
        raise ValueError("Verified purchase sensitivity CSV changed")
    for name in (completed_path.as_posix(), "reports/purchase_invoice_sensitivity.json",
                 prior["output_path"], completed["sales_report"]["path"],
                 "src/first_observed_purchases.py", "src/compare_first_observed_purchases.py",
                 "sql/08_first_observed_purchases.sql", "data/processed/sql/08_first_observed_purchases.sql"):
        inputs[name] = sha256(Path(name))
    for name, digest in inputs.items():
        if sha256(Path(name)) != digest:
            raise ValueError(f"Verified input changed: {name}")
    manifest = json.loads(Path("reports/load_manifest.json").read_text(encoding="utf-8"))
    cleaning = json.loads(Path("reports/cleaning_validation.json").read_text(encoding="utf-8"))
    with localcontext() as context:
        context.prec = 80
        invoices, source_checks = rebuild_purchase_inputs(raw_rows(manifest), load_code_rules(), manifest["null_marker"])
        expected_source = {"raw_rows": manifest["raw"]["rows"], "canonical_rows": manifest["canonical"]["rows"],
                           "excluded_overlap_rows": manifest["overlap_excluded"]["rows"],
                           "qualifying_lines_all_history": cleaning["stages"]["qualifying_purchase"]["rows"],
                           "qualifying_invoices_all_history": cleaning["qualifying_invoice_count_all_available_dates"]}
        for field, value in expected_source.items():
            if source_checks[field] != value:
                raise ValueError(f"Source reconciliation differs: {field}")
        if Decimal(source_checks["canonical_signed_amount_gbp"]) != Decimal(manifest["canonical"]["signed_amount_gbp"]):
            raise ValueError("Canonical signed amount differs")
        main = [invoice for invoice in invoices if invoice["invoice_date"] <= CUTOFF]
        known = cleaning["stages"]["main_history_qualifying_purchase"]
        known_anonymous = cleaning["stages"]["main_history_anonymous_purchase"]
        main_controls = {"purchase_invoices": len(main), "qualifying_lines": sum(i["lines"] for i in main),
                         "gross_gbp": str(sum((i["gross"] for i in main), ZERO)),
                         "identified_gross_gbp": str(sum((i["gross"] for i in main if i["customer_id"] is not None), ZERO)),
                         "anonymous_invoices": sum(i["customer_id"] is None for i in main),
                         "anonymous_lines": sum(i["anonymous_lines"] for i in main)}
        comparisons = {"purchase_invoices": known["invoices"], "qualifying_lines": known["rows"],
                       "gross_gbp": known["gross_merchandise_amount"],
                       "identified_gross_gbp": known["identified_gross_merchandise_amount"],
                       "anonymous_invoices": known_anonymous["invoices"],
                       "anonymous_lines": known_anonymous["rows"]}
        for field, value in comparisons.items():
            if Decimal(str(main_controls[field])) != Decimal(str(value)):
                raise ValueError(f"Prior main-history control differs: {field}")
        rows, day_controls = build_customer_histories(invoices)
        if day_controls["identified_customers_through_cutoff"] != known["identified_customers"]:
            raise ValueError("Customer history population differs from prior controls")
        if day_controls["identified_purchase_invoices_through_cutoff"] != len(main) - main_controls["anonymous_invoices"]:
            raise ValueError("Identified invoice count differs")
    for name, digest in inputs.items():
        if sha256(Path(name)) != digest:
            raise ValueError(f"Input changed during preparation: {name}")
    output = Path("reports/first_observed_purchases_expected.csv")
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    controls = history_controls(rows)
    evidence = {"generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                "execution": "Local source reconstruction only; no new BigQuery execution or dry run.",
                "milestone_4": "in progress", "cloud_export_comparison": "pending",
                "source_start": SOURCE_START.isoformat(), "reporting_cutoff_inclusive": CUTOFF.isoformat(),
                "output_path": output.as_posix(), "output_sha256": sha256(output), "output_bytes": output.stat().st_size,
                "expected_columns": COLUMNS, "expected_rows": len(rows),
                "expected_nonnull_field_cells": sum(row[field] != "" for row in rows for field in COLUMNS[1:]),
                "expected_null_field_cells": sum(row[field] == "" for row in rows for field in COLUMNS[1:]),
                "input_sha256_before_and_after": inputs, "all_inputs_unchanged": True,
                "source_checks": source_checks, "main_history_controls": main_controls,
                "customer_day_controls": day_controls, "history_controls": controls,
                "definition": "Identified qualifying purchase dates through cutoff; first observed and earliest strictly later calendar date. Days 1-90 inclusive. Full follow-up requires first date +90 <=cutoff. Every ineligible outcome is NULL, even if a repeat is already observed.",
                "limits": "Incomplete earlier history, particularly at December 2009 start; first observed is not acquisition. Later-day field is observed only through cutoff; NULL is not proof of never purchasing again. No cohort rates or business findings calculated in this step."}
    Path("reports/first_observed_purchases_local_controls.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"PASS: {len(rows)} customer histories; source/main-history and customer/day populations reconcile. Cloud comparison pending.")


if __name__ == "__main__":
    main()
