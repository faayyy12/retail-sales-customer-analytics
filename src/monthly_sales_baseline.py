"""Independent local monthly controls from verified raw CSVs; no cloud access."""

from collections import defaultdict
import csv
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cleaning_rules import classify, load_code_rules
from compare_category_controls import sha256
from reconcile_cleaning import add, finish, new_control, period_label


ZERO = Decimal(0)
COUNT_COLUMNS = ["purchase_invoice_count", "identified_purchasing_customers",
                 "identified_purchase_invoice_count", "anonymous_purchase_invoice_count"]
MONEY_COLUMNS = ["gross_merchandise_sales_gbp", "recorded_merchandise_credits_gbp",
                 "net_recorded_merchandise_sales_gbp", "identified_gross_merchandise_sales_gbp"]
RATIO_COLUMNS = ["average_purchase_invoice_value_gbp", "identified_gross_sales_share"]
COLUMNS = ["month_start", "gross_merchandise_sales_gbp", "recorded_merchandise_credits_gbp",
           "net_recorded_merchandise_sales_gbp", "purchase_invoice_count",
           "average_purchase_invoice_value_gbp", "identified_purchasing_customers",
           "identified_purchase_invoice_count", "anonymous_purchase_invoice_count",
           "identified_gross_merchandise_sales_gbp", "identified_gross_sales_share"]
MONTHS = [f"{year}-{month:02d}-01" for year in (2010, 2011) for month in range(1, 12)]


def ratio(numerator, denominator):
    """Match the SQL's explicit 9-place ratio precision; never round money sums."""
    if not denominator:
        return None
    with localcontext() as context:
        context.prec = 80
        return (Decimal(numerator) / Decimal(denominator)).quantize(
            Decimal("0.000000001"), rounding=ROUND_HALF_UP)


def month_control():
    return {"gross": ZERO, "credits": ZERO, "identified_gross": ZERO,
            "invoices": set(), "customers": set(), "identified_invoices": set(),
            "anonymous_invoices": set()}


def aggregate(rows, rules, null_marker):
    """Rebuild invoices and separately aggregate lines, preserving repeated rows."""
    categories = defaultdict(new_control)
    monthly_lines = defaultdict(month_control)
    invoices = {}
    raw_count = excluded_count = canonical_count = 0
    canonical_signed = ZERO
    for row in rows:
        raw_count += 1
        if row["source_sheet"] == "Year 2009-2010" and row["invoice_datetime"] >= "2010-12-01 00:00:00":
            excluded_count += 1
            continue
        canonical_count += 1
        quantity, price = int(row["quantity"]), Decimal(row["unit_price"])
        amount = quantity * price
        canonical_signed += amount
        customer = None if row["customer_id"] == null_marker else row["customer_id"]
        description = None if row["description_raw"] == null_marker else row["description_raw"]
        result = classify(row["stock_code_raw"], description, row["description_source_type"],
                          row["invoice_no"], quantity, price, rules)
        key = (period_label(row["invoice_datetime"]), result["item_category"], result["line_status"])
        add(categories[key], row, customer, amount, result)
        month = row["invoice_datetime"][:7] + "-01"
        if month in MONTHS:
            control = monthly_lines[month]
            control["credits"] += result["recorded_merchandise_credit_amount"]
            if result["line_status"] == "qualifying_purchase":
                control["gross"] += amount
                control["invoices"].add(row["invoice_no"])
                if customer is None:
                    control["anonymous_invoices"].add(row["invoice_no"])
                else:
                    control["customers"].add(customer)
                    control["identified_invoices"].add(row["invoice_no"])
                    control["identified_gross"] += amount
        if result["line_status"] == "qualifying_purchase":
            invoice = invoices.setdefault(row["invoice_no"], {
                "first_datetime": row["invoice_datetime"], "days": set(),
                "customers": set(), "countries": set(), "gross": ZERO})
            invoice["first_datetime"] = min(invoice["first_datetime"], row["invoice_datetime"])
            invoice["days"].add(row["invoice_datetime"][:10])
            invoice["customers"].add(customer)  # Include None to detect mixed ID presence.
            invoice["countries"].add(row["country"])
            invoice["gross"] += amount
    monthly_invoices = defaultdict(month_control)
    for number, invoice in invoices.items():
        if any(len(invoice[field]) != 1 for field in ("days", "customers", "countries")):
            raise ValueError(f"Conflicting qualifying invoice day/customer/country: {number}")
        month = invoice["first_datetime"][:7] + "-01"
        if month not in MONTHS:
            continue
        customer = next(iter(invoice["customers"]))
        control = monthly_invoices[month]
        control["gross"] += invoice["gross"]
        control["invoices"].add(number)
        if customer is None:
            control["anonymous_invoices"].add(number)
        else:
            control["identified_invoices"].add(number)
            control["customers"].add(customer)
            control["identified_gross"] += invoice["gross"]
    output, line_reconciliation = [], []
    for month in MONTHS:
        invoice, line = monthly_invoices[month], monthly_lines[month]
        for field in ("gross", "identified_gross", "invoices", "customers", "identified_invoices", "anonymous_invoices"):
            if invoice[field] != line[field]:
                raise ValueError(f"Invoice-to-line monthly control failed: {month}.{field}")
        count = len(invoice["invoices"])
        values = {
            "month_start": month,
            "gross_merchandise_sales_gbp": invoice["gross"],
            "recorded_merchandise_credits_gbp": line["credits"],
            "net_recorded_merchandise_sales_gbp": invoice["gross"] - line["credits"],
            "purchase_invoice_count": count,
            "average_purchase_invoice_value_gbp": ratio(invoice["gross"], count),
            "identified_purchasing_customers": len(invoice["customers"]),
            "identified_purchase_invoice_count": len(invoice["identified_invoices"]),
            "anonymous_purchase_invoice_count": len(invoice["anonymous_invoices"]),
            "identified_gross_merchandise_sales_gbp": invoice["identified_gross"],
            "identified_gross_sales_share": ratio(invoice["identified_gross"], invoice["gross"]),
        }
        output.append({key: "" if values[key] is None else str(values[key]) for key in COLUMNS})
        line_reconciliation.append({"month_start": month, "all_purchase_controls_match": True})
    evidence = {"raw_rows": raw_count, "excluded_overlap_rows": excluded_count,
                "canonical_rows": canonical_count, "canonical_signed_amount_gbp": str(canonical_signed),
                "qualifying_invoices_all_history": len(invoices),
                "invoice_day_customer_country_conflicts": 0,
                "monthly_invoice_to_line_checks": line_reconciliation}
    return output, categories, evidence


def raw_rows(manifest):
    for entry in manifest["raw_files"]:
        path = Path(entry["path"])
        count = 0
        with path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                count += 1
                yield row
        if count != entry["rows"]:
            raise ValueError(f"Raw file count mismatch: {path}")


def main():
    manifest_path = Path("reports/load_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    previous = json.loads(Path("reports/cleaning_validation.json").read_text(encoding="utf-8"))
    source = json.loads(Path("reports/source_manifest.json").read_text(encoding="utf-8"))
    inputs = {entry["path"]: entry["sha256"] for entry in manifest["raw_files"]}
    inputs.update({source["raw_path"]: source["sha256"], "docs/code_rules.csv": previous["code_rules_sha256"]})
    for name in ("reports/cleaning_category_controls.csv", "reports/bigquery_cleaning_category_controls.csv",
                 "reports/cleaning_category_comparison.json", "reports/load_manifest.json",
                 "reports/source_manifest.json", "reports/cleaning_validation.json",
                 "src/cleaning_rules.py", "src/monthly_sales_baseline.py",
                 "src/compare_monthly_sales.py", "sql/05_monthly_sales_baseline.sql",
                 "data/processed/sql/05_monthly_sales_baseline.sql"):
        inputs[name] = sha256(Path(name))
    for name, expected_hash in inputs.items():
        if sha256(Path(name)) != expected_hash:
            raise ValueError(f"Input checksum mismatch: {name}")
    if source["sha256"] != manifest["source_sha256"] or source["sha256"] != previous["source_sha256"]:
        raise ValueError("Source manifest chain mismatch")
    completed = json.loads(Path("reports/cleaning_category_comparison.json").read_text(encoding="utf-8"))
    if completed["status"] != "passed":
        raise ValueError("Cleaning category comparison must have passed")
    for label in ("expected", "observed"):
        info = completed[label]
        if sha256(Path(info["path"])) != info["sha256_after"]:
            raise ValueError(f"Cleaning comparison input changed: {label}")
    with localcontext() as context:
        context.prec = 80
        rows, categories, evidence = aggregate(raw_rows(manifest), load_code_rules(), manifest["null_marker"])
        with Path("reports/cleaning_category_controls.csv").open(encoding="utf-8", newline="") as stream:
            expected_categories = list(csv.DictReader(stream))
        expected_keys = {(row["period"], row["item_category"], row["line_status"]) for row in expected_categories}
        if len(expected_keys) != 63 or set(categories) != expected_keys:
            raise ValueError("Existing category key population differs")
        for expected in expected_categories:
            actual = finish(categories[(expected["period"], expected["item_category"], expected["line_status"])])
            for field, value in actual.items():
                if Decimal(str(value)) != Decimal(expected[field]):
                    raise ValueError(f"Existing category control differs: {expected['period']}.{field}")
        for field, value in (("raw_rows", manifest["raw"]["rows"]),
                             ("excluded_overlap_rows", manifest["overlap_excluded"]["rows"]),
                             ("canonical_rows", manifest["canonical"]["rows"]),
                             ("qualifying_invoices_all_history", previous["qualifying_invoice_count_all_available_dates"])):
            if evidence[field] != value:
                raise ValueError(f"Existing control differs: {field}")
        if Decimal(evidence["canonical_signed_amount_gbp"]) != Decimal(manifest["canonical"]["signed_amount_gbp"]):
            raise ValueError("Canonical signed control mismatch")
        period_controls = []
        for year in (2010, 2011):
            period_rows = [row for row in rows if row["month_start"].startswith(str(year))]
            category_rows = [finish(control) for key, control in categories.items() if key[0] == f"{year}_jan_nov"]
            for monthly_field, category_field in (
                ("gross_merchandise_sales_gbp", "gross_merchandise_amount"),
                ("recorded_merchandise_credits_gbp", "recorded_merchandise_credit_amount"),
                ("identified_gross_merchandise_sales_gbp", "identified_gross_merchandise_amount")):
                monthly = sum((Decimal(row[monthly_field]) for row in period_rows), ZERO)
                category = sum((Decimal(row[category_field]) for row in category_rows), ZERO)
                if monthly != category:
                    raise ValueError(f"Period control mismatch: {year}.{monthly_field}")
                period_controls.append({"period": f"{year}_jan_nov", "metric": monthly_field,
                                        "monthly_sum": str(monthly), "existing_category_sum": str(category), "matches": True})
            qualifying_controls = [finish(control) for key, control in categories.items()
                                   if key[0] == f"{year}_jan_nov" and key[2] == "qualifying_purchase"]
            if len(qualifying_controls) != 1:
                raise ValueError("Expected one merchandise purchase control per period")
            invoice_count = sum(int(row["purchase_invoice_count"]) for row in period_rows)
            if invoice_count != qualifying_controls[0]["invoices"]:
                raise ValueError(f"Period purchase invoice count mismatch: {year}")
            period_controls.append({"period": f"{year}_jan_nov", "metric": "purchase_invoice_count",
                                    "monthly_sum": invoice_count,
                                    "existing_category_count": qualifying_controls[0]["invoices"], "matches": True})
    for name, expected_hash in inputs.items():
        if sha256(Path(name)) != expected_hash:
            raise ValueError(f"Input changed during calculation: {name}")
    output = Path("reports/monthly_sales_expected.csv")
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    evidence.update({
        "generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
        "execution": "Local Python only; monthly GoogleSQL not executed or dry-run.",
        "cloud_monthly_reconciliation": "pending", "milestone_3": "in progress; not complete",
        "expected_path": output.as_posix(), "expected_sha256": sha256(output),
        "expected_rows": len(rows), "expected_columns": COLUMNS,
        "input_sha256_before_and_after": inputs, "all_inputs_unchanged": True,
        "existing_category_keys_recomputed": 63, "existing_category_numeric_matches": 504,
        "period_controls": period_controls,
        "money_method": "Decimal from original CSV price text times integer quantity; precision 80; no rounding of monetary sums.",
        "ratio_method": "Exact operands; Decimal division at precision 80, quantize 9 fractional digits ROUND_HALF_UP (half away from zero); blank if denominator zero.",
        "limitations": "Classification reuses established Python rules; monthly aggregation is independent of BigQuery. No business findings yet.",
    })
    Path("reports/monthly_sales_local_controls.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"PASS: {len(rows)} expected months; 63 existing category keys/504 values match; inputs unchanged. Cloud comparison pending.")


if __name__ == "__main__":
    main()
