"""Compute local cleaning controls from raw load files; do not execute GoogleSQL."""

from collections import defaultdict
import csv
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cleaning_rules import classify, load_code_rules
from verify_source import sha256


ZERO = Decimal("0")


def new_control():
    return {"rows": 0, "invoice_set": set(), "customer_set": set(), "missing_customer_id_rows": 0, "signed_amount_gbp": ZERO, "gross_merchandise_amount": ZERO, "recorded_merchandise_credit_amount": ZERO, "identified_gross_merchandise_amount": ZERO}


def add(control, row, customer, amount, result=None):
    control["rows"] += 1
    control["invoice_set"].add(row["invoice_no"])
    if customer is None:
        control["missing_customer_id_rows"] += 1
    else:
        control["customer_set"].add(customer)
    control["signed_amount_gbp"] += amount
    if result:
        control["gross_merchandise_amount"] += result["gross_merchandise_amount"]
        control["recorded_merchandise_credit_amount"] += result["recorded_merchandise_credit_amount"]
        if customer is not None:
            control["identified_gross_merchandise_amount"] += result["gross_merchandise_amount"]


def finish(control, classified=True):
    summary = {"rows": control["rows"], "invoices": len(control["invoice_set"]), "identified_customers": len(control["customer_set"]), **{k: str(v) if isinstance(v, Decimal) else v for k, v in control.items() if k not in {"rows", "invoice_set", "customer_set"}}}
    if not classified:
        for field in ["gross_merchandise_amount", "recorded_merchandise_credit_amount", "identified_gross_merchandise_amount"]:
            summary[field] = None
    return summary


def period_label(timestamp):
    if timestamp < "2010-01-01 00:00:00":
        return "2009_history"
    if timestamp < "2010-12-01 00:00:00":
        return "2010_jan_nov"
    if timestamp < "2011-01-01 00:00:00":
        return "2010_dec_history"
    if timestamp < "2011-12-01 00:00:00":
        return "2011_jan_nov"
    return "2011_dec_partial"


def main():
    reports = Path("reports")
    manifest = json.loads((reports / "load_manifest.json").read_text(encoding="utf-8"))
    rules = load_code_rules()
    rules_hash = sha256("docs/code_rules.csv")
    stages = defaultdict(new_control)
    categories = defaultdict(new_control)
    invoices = {}
    seen = set()
    repeated = new_control()
    examples = []
    normalized_codes = 0
    trimmed_descriptions = 0
    for entry in manifest["raw_files"]:
        path = Path(entry["path"])
        if sha256(path) != entry["sha256"]:
            raise ValueError(f"Load file changed since preparation: {path}")
        print(f"Reconciling cleaning rules for {path.name}...", flush=True)
        with path.open(encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                customer = None if row["customer_id"] == manifest["null_marker"] else row["customer_id"]
                description = None if row["description_raw"] == manifest["null_marker"] else row["description_raw"]
                quantity = int(row["quantity"])
                price = Decimal(row["unit_price"])
                amount = quantity * price
                add(stages["raw"], row, customer, amount)
                if row["source_sheet"] == "Year 2009-2010" and row["invoice_datetime"] >= "2010-12-01 00:00:00":
                    add(stages["excluded_overlap"], row, customer, amount)
                    continue
                result = classify(row["stock_code_raw"], description, row["description_source_type"], row["invoice_no"], quantity, price, rules)
                add(stages["canonical_all_classified_rows"], row, customer, amount, result)
                add(stages[result["line_status"]], row, customer, amount, result)
                if row["invoice_datetime"] < "2011-12-01 00:00:00":
                    add(stages["main_history_all_classified_rows"], row, customer, amount, result)
                    if result["line_status"] == "qualifying_purchase":
                        add(stages["main_history_qualifying_purchase"], row, customer, amount, result)
                        if customer is None:
                            add(stages["main_history_anonymous_purchase"], row, customer, amount, result)
                normalized_codes += row["stock_code_raw"] != result["stock_code"]
                trimmed_descriptions += description is not None and description != result["description"]
                key = (period_label(row["invoice_datetime"]), result["item_category"], result["line_status"])
                add(categories[key], row, customer, amount, result)
                business_key = (row["invoice_no"], row["stock_code_raw"], description, row["description_source_type"], quantity, row["invoice_datetime"], price, customer, row["country"])
                if business_key in seen:
                    add(repeated, row, customer, amount, result)
                else:
                    seen.add(business_key)
                if result["line_status"] in {"negative_price_adjustment", "unresolved_description", "unresolved_sign_or_invoice"} and len(examples) < 50:
                    examples.append({**row, **result})
                if result["line_status"] == "qualifying_purchase":
                    invoice = invoices.setdefault(row["invoice_no"], {"customers": set(), "countries": set(), "days": set(), "first_datetime": row["invoice_datetime"], "last_datetime": row["invoice_datetime"], "lines": 0, "gross": ZERO})
                    if customer is not None:
                        invoice["customers"].add(customer)
                    invoice["countries"].add(row["country"])
                    invoice["days"].add(row["invoice_datetime"][:10])
                    invoice["first_datetime"] = min(invoice["first_datetime"], row["invoice_datetime"])
                    invoice["last_datetime"] = max(invoice["last_datetime"], row["invoice_datetime"])
                    invoice["lines"] += 1
                    invoice["gross"] += result["gross_merchandise_amount"]
    invoice_conflicts = [key for key, v in invoices.items() if len(v["customers"]) > 1 or len(v["days"]) != 1 or len(v["countries"]) != 1]
    if invoice_conflicts:
        raise ValueError(f"Qualifying invoices need date/customer/country resolution: {invoice_conflicts[:10]}")
    for stage, source_key in [("raw", "raw"), ("excluded_overlap", "overlap_excluded"), ("canonical_all_classified_rows", "canonical")]:
        summary = finish(stages[stage])
        for key in ["rows", "invoices", "identified_customers", "missing_customer_id_rows", "signed_amount_gbp"]:
            if summary[key] != manifest[source_key][key]:
                raise ValueError(f"Local control differs from load manifest: {stage}.{key}")
    if repeated["rows"] != manifest["canonical_exact_repeat_excess_rows_retained"]:
        raise ValueError("Repeat-row diagnostic differs from source preparation.")
    gross = sum((v["gross"] for v in invoices.values()), ZERO)
    if gross != stages["qualifying_purchase"]["gross_merchandise_amount"]:
        raise ValueError("Invoice-to-line monetary reconciliation failed.")
    category_rows = [{"period": k[0], "item_category": k[1], "line_status": k[2], **finish(v)} for k, v in sorted(categories.items())]
    with (reports / "cleaning_category_controls.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(category_rows[0]))
        writer.writeheader()
        writer.writerows(category_rows)
    stage_rows = [{"stage": key, **finish(value, classified=key not in {"raw", "excluded_overlap"})} for key, value in stages.items()]
    with (reports / "cleaning_effects.csv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(stage_rows[0]))
        writer.writeheader()
        writer.writerows(stage_rows)
    if examples:
        with (reports / "cleaning_unresolved_examples.csv").open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(examples[0]))
            writer.writeheader()
            writer.writerows(examples)
    histories = stages["main_history_all_classified_rows"]
    result = {
        "generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
        "source_sha256": manifest["source_sha256"], "code_rules_sha256": rules_hash,
        "execution": "Local Python reconciliation only. GoogleSQL has not run in BigQuery.",
        "bigquery_executed": False, "stages": {key: finish(value, classified=key not in {"raw", "excluded_overlap"}) for key, value in stages.items()},
        "stock_code_normalization_changed_rows": normalized_codes,
        "description_trimming_changed_rows": trimmed_descriptions,
        "qualifying_invoice_count_all_available_dates": len(invoices),
        "qualifying_invoices_multiple_timestamps_same_day": sum(v["first_datetime"] != v["last_datetime"] for v in invoices.values()),
        "qualifying_invoice_conflicts": len(invoice_conflicts),
        "within_sheet_repeat_sensitivity": {"assumption": "Hypothetical removal of exact repeat excess after overlap; NOT applied.", **finish(repeated)},
        "main_history_customer_id_gross_sales_coverage": str(histories["identified_gross_merchandise_amount"] / histories["gross_merchandise_amount"]),
        "verification": {"raw_file_checksums": True, "canonical_and_exclusion_counts_and_amounts": True, "all_categories_preserve_rows_and_amounts": True, "qualifying_invoice_keys_and_date_customer_country_consistency": True, "invoice_gross_matches_lines_exactly": True, "repeated_lines_retained": True},
    }
    if sum(v["rows"] for v in categories.values()) != stages["canonical_all_classified_rows"]["rows"]:
        raise ValueError("Category partition does not preserve rows.")
    if sum((v["signed_amount_gbp"] for v in categories.values()), ZERO) != stages["canonical_all_classified_rows"]["signed_amount_gbp"]:
        raise ValueError("Category partition does not preserve signed amounts.")
    (reports / "cleaning_validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("Local cleaning controls reconciled; BigQuery verification remains pending.", flush=True)


if __name__ == "__main__":
    main()
