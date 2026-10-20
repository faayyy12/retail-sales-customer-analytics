"""Independent full-period identified sales controls; no cloud access."""

from collections import defaultdict
import csv
from datetime import datetime
from decimal import Decimal, localcontext
from fractions import Fraction
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cleaning_rules import classify, load_code_rules
from compare_category_controls import sha256
from monthly_sales_baseline import MONTHS, raw_rows, ratio


PERIODS = ["2010_jan_nov", "2011_jan_nov"]
COUNT_COLUMNS = ["identified_purchasing_customers", "identified_purchase_invoice_count",
                 "anonymous_purchase_invoice_count", "total_purchase_invoice_count"]
MONEY_COLUMNS = ["identified_gross_merchandise_sales_gbp", "anonymous_gross_merchandise_sales_gbp",
                 "total_gross_merchandise_sales_gbp"]
RATIO_COLUMNS = ["purchases_per_identified_customer", "average_identified_purchase_invoice_value_gbp",
                 "identified_gross_sales_share"]
COLUMNS = ["period", "identified_purchasing_customers", "identified_purchase_invoice_count",
           "purchases_per_identified_customer", "average_identified_purchase_invoice_value_gbp",
           "identified_gross_merchandise_sales_gbp", "anonymous_purchase_invoice_count",
           "anonymous_gross_merchandise_sales_gbp", "total_purchase_invoice_count",
           "total_gross_merchandise_sales_gbp", "identified_gross_sales_share"]
ZERO = Decimal(0)


def period_for(timestamp):
    if "2010-01-01" <= timestamp < "2010-12-01":
        return PERIODS[0]
    if "2011-01-01" <= timestamp < "2011-12-01":
        return PERIODS[1]
    return None


def new_control():
    return {"customers": set(), "identified_invoices": set(), "anonymous_invoices": set(),
            "identified_gross": ZERO, "anonymous_gross": ZERO}


def add_purchase(control, number, customer, gross):
    if customer is None:
        control["anonymous_invoices"].add(number)
        control["anonymous_gross"] += gross
    else:
        control["customers"].add(customer)
        control["identified_invoices"].add(number)
        control["identified_gross"] += gross


def exact_identity(row):
    """Preserve the exact rational factors, not rounded export quotients."""
    customers = int(Decimal(row["identified_purchasing_customers"]))
    invoices = int(Decimal(row["identified_purchase_invoice_count"]))
    gross = Fraction(row["identified_gross_merchandise_sales_gbp"])
    if customers == 0 or invoices == 0:
        return {"defined": False, "identity_matches": None,
                "reason": "No identified purchasing population; factors are undefined, not zero."}
    frequency = Fraction(invoices, customers)
    value = gross / invoices
    product = customers * frequency * value
    return {"defined": True, "customers": customers,
            "purchases_per_customer_fraction": str(frequency),
            "average_invoice_value_fraction_gbp": str(value),
            "exact_product_fraction_gbp": str(product),
            "identified_gross_fraction_gbp": str(gross),
            "exact_residual_fraction_gbp": str(product - gross),
            "identity_matches": product == gross}


def aggregate(rows, rules, null_marker):
    """Rebuild qualifying invoices and distinct full-period line populations."""
    invoices = {}
    line_controls = defaultdict(new_control)
    raw_count = excluded_count = canonical_count = qualifying_count = 0
    canonical_signed = ZERO
    for row in rows:
        raw_count += 1
        if row["source_sheet"] == "Year 2009-2010" and row["invoice_datetime"] >= "2010-12-01 00:00:00":
            excluded_count += 1
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
        gross = result["gross_merchandise_amount"]
        period = period_for(row["invoice_datetime"])
        if period:
            add_purchase(line_controls[period], row["invoice_no"], customer, gross)
        invoice = invoices.setdefault(row["invoice_no"], {
            "first_datetime": row["invoice_datetime"], "days": set(), "customers": set(),
            "countries": set(), "gross": ZERO})
        invoice["first_datetime"] = min(invoice["first_datetime"], row["invoice_datetime"])
        invoice["days"].add(row["invoice_datetime"][:10])
        invoice["customers"].add(customer)
        invoice["countries"].add(row["country"])
        invoice["gross"] += gross
    invoice_controls = defaultdict(new_control)
    for number, invoice in invoices.items():
        if any(len(invoice[field]) != 1 for field in ("days", "customers", "countries")):
            raise ValueError(f"Conflicting qualifying invoice day/customer/country: {number}")
        period = period_for(invoice["first_datetime"])
        if period:
            add_purchase(invoice_controls[period], number, next(iter(invoice["customers"])), invoice["gross"])
    output, checks = [], []
    for period in PERIODS:
        control = invoice_controls[period]
        if control != line_controls[period]:
            raise ValueError(f"Full-period invoice/line populations or amounts differ: {period}")
        customers, identified = len(control["customers"]), len(control["identified_invoices"])
        anonymous = len(control["anonymous_invoices"])
        if control["identified_invoices"] & control["anonymous_invoices"]:
            raise ValueError("Invoice assigned to identified and anonymous populations")
        gross = control["identified_gross"] + control["anonymous_gross"]
        values = {
            "period": period, "identified_purchasing_customers": customers,
            "identified_purchase_invoice_count": identified,
            "purchases_per_identified_customer": ratio(identified, customers),
            "average_identified_purchase_invoice_value_gbp": ratio(control["identified_gross"], identified),
            "identified_gross_merchandise_sales_gbp": control["identified_gross"],
            "anonymous_purchase_invoice_count": anonymous,
            "anonymous_gross_merchandise_sales_gbp": control["anonymous_gross"],
            "total_purchase_invoice_count": identified + anonymous,
            "total_gross_merchandise_sales_gbp": gross,
            "identified_gross_sales_share": ratio(control["identified_gross"], gross),
        }
        row = {field: "" if values[field] is None else str(values[field]) for field in COLUMNS}
        output.append(row)
        checks.append({"period": period, "invoice_to_line_population_and_money_match": True,
                       "exact_identity": exact_identity(row)})
    return output, {"raw_rows": raw_count, "excluded_overlap_rows": excluded_count,
                    "canonical_rows": canonical_count, "canonical_signed_amount_gbp": str(canonical_signed),
                    "qualifying_lines_all_history": qualifying_count,
                    "qualifying_invoices_all_history": len(invoices),
                    "invoice_day_customer_country_conflicts": 0, "period_checks": checks}


def baseline_controls(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 22 or sorted(row["month_start"] for row in rows) != MONTHS:
        raise ValueError("Baseline must have the 22 unique verified months")
    controls = {}
    for period in PERIODS:
        selected = [row for row in rows if row["month_start"].startswith(period[:4])]
        controls[period] = {
            "identified_purchase_invoice_count": sum(int(row["identified_purchase_invoice_count"]) for row in selected),
            "anonymous_purchase_invoice_count": sum(int(row["anonymous_purchase_invoice_count"]) for row in selected),
            "total_purchase_invoice_count": sum(int(row["purchase_invoice_count"]) for row in selected),
            "identified_gross_merchandise_sales_gbp": sum((Decimal(row["identified_gross_merchandise_sales_gbp"]) for row in selected), ZERO),
            "total_gross_merchandise_sales_gbp": sum((Decimal(row["gross_merchandise_sales_gbp"]) for row in selected), ZERO),
        }
        values = controls[period]
        values["anonymous_gross_merchandise_sales_gbp"] = values["total_gross_merchandise_sales_gbp"] - values["identified_gross_merchandise_sales_gbp"]
    return controls


def main():
    manifest = json.loads(Path("reports/load_manifest.json").read_text(encoding="utf-8"))
    previous = json.loads(Path("reports/monthly_sales_local_controls.json").read_text(encoding="utf-8"))
    comparison = json.loads(Path("reports/monthly_sales_comparison.json").read_text(encoding="utf-8"))
    if comparison["status"] != "passed" or comparison["numeric_comparisons_total"] != 220:
        raise ValueError("The monthly baseline must first pass all 220 comparisons")
    inputs = dict(previous["input_sha256_before_and_after"])
    inputs[previous["expected_path"]] = previous["expected_sha256"]
    for label in ("expected", "observed"):
        info = comparison[label]
        if info["path"] in inputs and inputs[info["path"]] != info["sha256_after"]:
            raise ValueError("Baseline provenance chain differs")
        inputs[info["path"]] = info["sha256_after"]
    for name in ("reports/monthly_sales_local_controls.json", "reports/monthly_sales_comparison.json",
                 "src/identified_sales_breakdown.py", "src/compare_identified_sales.py",
                 "sql/06_identified_sales_breakdown.sql", "data/processed/sql/06_identified_sales_breakdown.sql"):
        inputs[name] = sha256(Path(name))
    for name, digest in inputs.items():
        if sha256(Path(name)) != digest:
            raise ValueError(f"Verified input changed: {name}")
    with localcontext() as context:
        context.prec = 80
        rows, evidence = aggregate(raw_rows(manifest), load_code_rules(), manifest["null_marker"])
        for field, value in (("raw_rows", manifest["raw"]["rows"]),
                             ("excluded_overlap_rows", manifest["overlap_excluded"]["rows"]),
                             ("canonical_rows", manifest["canonical"]["rows"]),
                             ("qualifying_invoices_all_history", previous["qualifying_invoices_all_history"])):
            if evidence[field] != value:
                raise ValueError(f"Existing population differs: {field}")
        if Decimal(evidence["canonical_signed_amount_gbp"]) != Decimal(manifest["canonical"]["signed_amount_gbp"]):
            raise ValueError("Canonical monetary control differs")
        baseline = baseline_controls(Path(comparison["observed"]["path"]))
        with Path("reports/cleaning_category_controls.csv").open(encoding="utf-8", newline="") as stream:
            categories = [row for row in csv.DictReader(stream) if row["line_status"] == "qualifying_purchase" and row["period"] in PERIODS]
        if len(categories) != 2 or {row["period"] for row in categories} != set(PERIODS):
            raise ValueError("Expected one qualifying-merchandise category control per period")
        for row in rows:
            for field, value in baseline[row["period"]].items():
                if Decimal(row[field]) != value:
                    raise ValueError(f"Verified monthly baseline differs: {row['period']}.{field}")
            category = next(item for item in categories if item["period"] == row["period"])
            if int(row["identified_purchasing_customers"]) != int(category["identified_customers"]):
                raise ValueError("Independent full-period distinct customer count differs from category controls")
        changes = {}
        # Compute changes from exact fractions, never the rounded CSV quotient fields.
        for field in ("identified_purchasing_customers", "identified_purchase_invoice_count", "identified_gross_merchandise_sales_gbp"):
            a, b = Fraction(rows[0][field]), Fraction(rows[1][field])
            change = 100 * (b - a) / a
            changes[field] = {"earlier_exact": str(a), "later_exact": str(b),
                              "percent_change": str(ratio(Decimal(change.numerator), Decimal(change.denominator)))}
        for field, numerator, denominator in (
            ("purchases_per_identified_customer", "identified_purchase_invoice_count", "identified_purchasing_customers"),
            ("average_identified_purchase_invoice_value_gbp", "identified_gross_merchandise_sales_gbp", "identified_purchase_invoice_count")):
            a, b = (Fraction(row[numerator]) / Fraction(row[denominator]) for row in rows)
            change = 100 * (b - a) / a
            changes[field] = {"earlier_exact_fraction": str(a), "later_exact_fraction": str(b),
                              "percent_change": str(ratio(Decimal(change.numerator), Decimal(change.denominator)))}
        c0, c1 = (Fraction(row["identified_purchasing_customers"]) for row in rows)
        i0, i1 = (Fraction(row["identified_purchase_invoice_count"]) for row in rows)
        g0, g1 = (Fraction(row["identified_gross_merchandise_sales_gbp"]) for row in rows)
        factor_product = (c1 / c0) * ((i1 / c1) / (i0 / c0)) * ((g1 / i1) / (g0 / i0))
        growth_identity = {"factor_product_exact_fraction": str(factor_product),
                           "gross_sales_ratio_exact_fraction": str(g1 / g0),
                           "exact_residual_fraction": str(factor_product - g1 / g0),
                           "matches": factor_product == g1 / g0}
        if not growth_identity["matches"]:
            raise ValueError("Year-over-year factor identity failed")
    for name, digest in inputs.items():
        if sha256(Path(name)) != digest:
            raise ValueError(f"Input changed during calculation: {name}")
    output = Path("reports/identified_sales_expected.csv")
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    evidence.update({"generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                     "execution": "Local only; identified-breakdown GoogleSQL not submitted or dry-run.",
                     "cloud_comparison": "pending", "milestone_3": "in progress",
                     "expected_path": output.as_posix(), "expected_sha256": sha256(output),
                     "input_sha256_before_and_after": inputs, "all_inputs_unchanged": True,
                     "baseline_period_controls": {period: {field: str(value) for field, value in values.items()}
                                                  for period, values in baseline.items()},
                     "baseline_numeric_matches": 12, "existing_full_period_customer_count_matches": 2,
                     "year_over_year_local_expectations": changes,
                     "year_over_year_factor_identity": growth_identity,
                     "identity_method": "Exact Fraction C * (I/C) * (G/I), zero rational residual. Export quotients are 9-decimal approximations and are not used for this check.",
                     "money_method": "Decimal original CSV price times integer quantity; precision 80; no rounding of money totals."})
    Path("reports/identified_sales_local_controls.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("PASS: two full periods; exact rational identities; 12 baseline amount/count controls and two prior customer controls match. Cloud comparison pending.")


if __name__ == "__main__":
    main()
