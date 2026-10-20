"""Purchase-invoice concentration and size sensitivity; no cloud access."""

import csv
from datetime import datetime
from decimal import Decimal, localcontext
from fractions import Fraction
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cleaning_rules import classify, load_code_rules
from compare_category_controls import sha256
from identified_sales_breakdown import PERIODS, period_for
from monthly_sales_baseline import raw_rows, ratio


POPULATIONS = ("all", "identified", "anonymous")
COLUMNS = ["period", "population", "top_n", "invoice_count", "gross_gbp",
           "average_invoice_value_gbp", "median_invoice_value_gbp", "top_invoice_count",
           "top_gross_gbp", "top_gross_share", "remainder_invoice_count",
           "remainder_gross_gbp", "remainder_average_invoice_value_gbp"]
ZERO = Decimal(0)


def median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def concentration(invoices):
    rows, top_examples = [], []
    for period in PERIODS:
        for population in POPULATIONS:
            selected = [invoice for invoice in invoices if invoice["period"] == period and
                        (population == "all" or (invoice["has_customer_id"] == (population == "identified")))]
            selected.sort(key=lambda item: (-item["gross"], item["invoice_no"]))
            gross = sum((item["gross"] for item in selected), ZERO)
            for n in (1, 10):
                top, rest = selected[:n], selected[n:]
                top_gross = sum((item["gross"] for item in top), ZERO)
                remainder = sum((item["gross"] for item in rest), ZERO)
                if top_gross + remainder != gross or len(top) + len(rest) != len(selected):
                    raise ValueError("Top and remainder do not preserve the full population")
                values = {"period": period, "population": population, "top_n": n,
                          "invoice_count": len(selected), "gross_gbp": gross,
                          "average_invoice_value_gbp": ratio(gross, len(selected)),
                          "median_invoice_value_gbp": median([item["gross"] for item in selected]),
                          "top_invoice_count": len(top), "top_gross_gbp": top_gross,
                          "top_gross_share": ratio(top_gross, gross),
                          "remainder_invoice_count": len(rest), "remainder_gross_gbp": remainder,
                          "remainder_average_invoice_value_gbp": ratio(remainder, len(rest))}
                rows.append({key: "" if values[key] is None else str(values[key]) for key in COLUMNS})
            if population == "all":
                top_examples.extend({"period": period, "rank": rank, "invoice_no": item["invoice_no"],
                                     "gross_gbp": str(item["gross"]), "has_customer_id": item["has_customer_id"],
                                     "recorded_date": item["recorded_date"]}
                                    for rank, item in enumerate(selected[:10], 1))
    return rows, top_examples


def main():
    prior = json.loads(Path("reports/recorded_credit_local_controls.json").read_text(encoding="utf-8"))
    inputs = dict(prior["input_sha256_before_and_after"])
    for filename, required in (("reports/monthly_sales_comparison.json", 220),
                               ("reports/identified_sales_comparison.json", 20),
                               ("reports/recorded_credit_comparison.json", 312)):
        result = json.loads(Path(filename).read_text(encoding="utf-8"))
        if result["status"] != "passed" or result["numeric_comparisons_total"] != required:
            raise ValueError(f"Prior verification is not passed: {filename}")
        if required == 312 and result["comparisons"]["null_cells"] != 84:
            raise ValueError("Credit NULL controls differ")
        for label in ("expected", "observed"):
            info = result[label]
            if info["path"] in inputs and inputs[info["path"]] != info["sha256_after"]:
                raise ValueError("Existing verification chain differs")
            inputs[info["path"]] = info["sha256_after"]
        inputs[filename] = sha256(Path(filename))
    inputs["reports/recorded_credit_local_controls.json"] = sha256(Path("reports/recorded_credit_local_controls.json"))
    inputs["src/review_purchase_concentration.py"] = sha256(Path("src/review_purchase_concentration.py"))
    for name, digest in inputs.items():
        if sha256(Path(name)) != digest:
            raise ValueError(f"Verified input changed: {name}")
    manifest = json.loads(Path("reports/load_manifest.json").read_text(encoding="utf-8"))
    cleaning = json.loads(Path("reports/cleaning_validation.json").read_text(encoding="utf-8"))
    rules, invoices = load_code_rules(), {}
    raw_count = canonical_count = qualifying_lines = 0
    canonical_signed = ZERO
    with localcontext() as context:
        context.prec = 80
        for row in raw_rows(manifest):
            raw_count += 1
            if row["source_sheet"] == "Year 2009-2010" and row["invoice_datetime"] >= "2010-12-01 00:00:00":
                continue
            canonical_count += 1
            quantity, price = int(row["quantity"]), Decimal(row["unit_price"])
            canonical_signed += quantity * price
            description = None if row["description_raw"] == manifest["null_marker"] else row["description_raw"]
            result = classify(row["stock_code_raw"], description, row["description_source_type"],
                              row["invoice_no"], quantity, price, rules)
            if result["line_status"] != "qualifying_purchase":
                continue
            qualifying_lines += 1
            invoice = invoices.setdefault(row["invoice_no"], {"gross": ZERO, "dates": set(), "customers": set(), "countries": set()})
            invoice["gross"] += result["gross_merchandise_amount"]
            invoice["dates"].add(row["invoice_datetime"][:10])
            invoice["customers"].add(None if row["customer_id"] == manifest["null_marker"] else row["customer_id"])
            invoice["countries"].add(row["country"])
        if (raw_count != manifest["raw"]["rows"] or canonical_count != manifest["canonical"]["rows"]
                or canonical_signed != Decimal(manifest["canonical"]["signed_amount_gbp"])
                or qualifying_lines != cleaning["stages"]["qualifying_purchase"]["rows"]
                or len(invoices) != cleaning["qualifying_invoice_count_all_available_dates"]):
            raise ValueError("Source/qualifying population reconciliation failed")
        selected = []
        for number, invoice in invoices.items():
            if any(len(invoice[field]) != 1 for field in ("dates", "customers", "countries")):
                raise ValueError(f"Conflicting invoice calendar date/customer/country: {number}")
            date = next(iter(invoice["dates"]))
            period = period_for(date)
            if period:
                selected.append({"invoice_no": number, "period": period, "gross": invoice["gross"],
                                 "has_customer_id": next(iter(invoice["customers"])) is not None, "recorded_date": date})
        rows, examples = concentration(selected)
        with Path("reports/bigquery_identified_sales_breakdown.csv").open(encoding="utf-8-sig", newline="") as stream:
            baseline = {row["period"]: row for row in csv.DictReader(stream)}
        for row in rows:
            population = row["population"]
            prefix = {"all": "total", "identified": "identified", "anonymous": "anonymous"}[population]
            expected = baseline[row["period"]]
            if (int(row["invoice_count"]) != int(expected[f"{prefix}_purchase_invoice_count"])
                    or Decimal(row["gross_gbp"]) != Decimal(expected[f"{prefix}_gross_merchandise_sales_gbp"])):
                raise ValueError("Rebuilt invoice population differs from verified baseline")
        changes = []
        for population in POPULATIONS:
            for n in (1, 10):
                a, b = (next(row for row in rows if row["period"] == p and row["population"] == population and int(row["top_n"]) == n) for p in PERIODS)
                earlier = Fraction(a["remainder_gross_gbp"]) / int(a["remainder_invoice_count"])
                later = Fraction(b["remainder_gross_gbp"]) / int(b["remainder_invoice_count"])
                change = 100 * (later - earlier) / earlier
                changes.append({"population": population, "largest_n_separated_each_year": n,
                                "remainder_aiv_change_percent": str(ratio(Decimal(change.numerator), Decimal(change.denominator)))})
    for name, digest in inputs.items():
        if sha256(Path(name)) != digest:
            raise ValueError(f"Input changed during review: {name}")
    output = Path("reports/purchase_invoice_sensitivity.csv")
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    evidence = {"checked_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                "scope": "Local source-based sensitivity only; no new cloud query or cloud export.",
                "input_sha256_before_and_after": inputs, "all_inputs_unchanged": True,
                "source_checks": {"raw_rows": raw_count, "canonical_rows": canonical_count,
                                  "qualifying_lines": qualifying_lines, "all_history_purchase_invoices": len(invoices),
                                  "invoice_date_customer_country_conflicts": 0,
                                  "all_period_population_counts_and_gross_match_verified_baseline": True},
                "output_path": output.as_posix(), "output_sha256": sha256(output),
                "top_purchase_invoice_examples": examples, "remainder_aiv_changes": changes,
                "method": "Exact Decimal sums and medians; integer invoice counts; exact Fraction ratio changes; 9-decimal ratio serialization. Sort descending gross, ascending invoice code for ties.",
                "limitations": "Top-1 and top-10 separations are diagnostic partitions, not data exclusions or replacement sales KPIs. Each year's population may contain different customers. Local details are not newly cloud-verified."}
    Path("reports/purchase_invoice_sensitivity.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("PASS: source invoices reconcile to all verified period/population totals; local top-1/top-10 and remainder sensitivity saved.")


if __name__ == "__main__":
    main()
