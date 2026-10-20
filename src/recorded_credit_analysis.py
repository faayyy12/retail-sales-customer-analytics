"""Local recorded-credit investigation from verified source CSVs; no cloud access."""

from collections import defaultdict
import csv
from datetime import datetime
from decimal import Decimal, localcontext
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cleaning_rules import classify, load_code_rules
from compare_category_controls import sha256
from identified_sales_breakdown import PERIODS, period_for
from monthly_sales_baseline import MONTHS, raw_rows, ratio


KEY_COLUMNS = ["section", "period", "detail_key"]
COUNT_COLUMNS = ["row_rank", "credit_invoice_count", "credit_line_count"]
MONEY_COLUMNS = ["recorded_credits_gbp"]
RATIO_COLUMNS = ["average_credit_per_invoice_gbp", "share_of_period_credits"]
COLUMNS = KEY_COLUMNS + COUNT_COLUMNS + MONEY_COLUMNS + RATIO_COLUMNS
ZERO = Decimal(0)
REMAINDER = "__REMAINDER__"
SECTION_ORDER = {"period_summary": 0, "invoice_concentration": 1, "stock_change": 2}


def extract_credits(rows, rules, null_marker):
    credits = []
    raw_count = excluded_count = canonical_count = all_credit_count = 0
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
        if result["line_status"] != "recorded_merchandise_credit":
            continue
        all_credit_count += 1
        period = period_for(row["invoice_datetime"])
        if period:
            credits.append({"period": period, "invoice_no": row["invoice_no"],
                            "stock_code": result["stock_code"], "date": row["invoice_datetime"][:10],
                            "amount": result["recorded_merchandise_credit_amount"],
                            "quantity": quantity, "unit_price": str(price), "description": description,
                            "source_sheet": row["source_sheet"], "source_row": row["source_row"]})
    return credits, {"raw_rows": raw_count, "excluded_overlap_rows": excluded_count,
                     "canonical_rows": canonical_count, "canonical_signed_amount_gbp": str(canonical_signed),
                     "credit_lines_all_history": all_credit_count}


def build_outputs(credits, top_n=10):
    """Independent line, invoice, stock and month sums; top lists always have remainders."""
    invoices, stocks = {}, {}
    months = {month: ZERO for month in MONTHS}
    period_totals = {period: {"amount": ZERO, "lines": 0, "invoices": set()} for period in PERIODS}
    for line in credits:
        period, number, code = line["period"], line["invoice_no"], line["stock_code"]
        if number in (REMAINDER, "__ALL__") or code == REMAINDER:
            raise ValueError("Source key collides with a summary label")
        if line["amount"] <= 0:
            raise ValueError("Classified credit magnitudes must be positive")
        total = period_totals[period]
        total["amount"] += line["amount"]
        total["lines"] += 1
        total["invoices"].add(number)
        months[line["date"][:7] + "-01"] += line["amount"]
        invoice = invoices.setdefault((period, number), {"amount": ZERO, "lines": 0, "dates": set(), "codes": set()})
        invoice["amount"] += line["amount"]
        invoice["lines"] += 1
        invoice["dates"].add(line["date"])
        invoice["codes"].add(code)
        stock = stocks.setdefault(code, {period: {"amount": ZERO, "lines": 0} for period in PERIODS})
        stock[period]["amount"] += line["amount"]
        stock[period]["lines"] += 1
    output = []
    def append(section, period, key, rank, count, lines, amount):
        average = None if count is None else ratio(amount, count)
        share = ratio(amount, period_totals[period]["amount"])
        values = {"section": section, "period": period, "detail_key": key, "row_rank": rank,
                  "credit_invoice_count": count, "credit_line_count": lines, "recorded_credits_gbp": amount,
                  "average_credit_per_invoice_gbp": average, "share_of_period_credits": share}
        output.append({field: "" if values[field] is None else str(values[field]) for field in COLUMNS})
    concentration = {}
    for period in PERIODS:
        total = period_totals[period]
        append("period_summary", period, "__ALL__", 0, len(total["invoices"]), total["lines"], total["amount"])
        ranked = sorted(((number, invoice) for (p, number), invoice in invoices.items() if p == period),
                        key=lambda item: (-item[1]["amount"], item[0]))
        for rank, (number, invoice) in enumerate(ranked[:top_n], 1):
            append("invoice_concentration", period, number, rank, 1, invoice["lines"], invoice["amount"])
        remaining = ranked[top_n:]
        append("invoice_concentration", period, REMAINDER, top_n + 1, len(remaining),
               sum(v["lines"] for _, v in remaining), sum((v["amount"] for _, v in remaining), ZERO))
        concentration[period] = {}
        for n in (1, 5, 10):
            selected = ranked[:n]
            amount = sum((v["amount"] for _, v in selected), ZERO)
            concentration[period][f"top_{n}"] = {"invoices": len(selected), "amount_gbp": str(amount),
                                                "share": str(ratio(amount, total["amount"])),
                                                "remainder_invoices": len(ranked) - len(selected),
                                                "remainder_gbp": str(total["amount"] - amount)}
        if sum((v["amount"] for _, v in ranked), ZERO) != total["amount"]:
            raise ValueError("Invoice aggregation lost credit amount")
    deltas = {code: values[PERIODS[1]]["amount"] - values[PERIODS[0]]["amount"] for code, values in stocks.items()}
    increases = sorted((code for code, change in deltas.items() if change > 0), key=lambda code: (-deltas[code], code))[:top_n]
    decreases = sorted((code for code, change in deltas.items() if change < 0), key=lambda code: (deltas[code], code))[:top_n]
    selected = [(code, rank) for rank, code in enumerate(increases, 1)] + [(code, top_n + rank) for rank, code in enumerate(decreases, 1)]
    remaining_codes = set(stocks) - {code for code, _ in selected}
    for period in PERIODS:
        for code, rank in selected:
            values = stocks[code][period]
            append("stock_change", period, code, rank, None, values["lines"], values["amount"])
        append("stock_change", period, REMAINDER, 2 * top_n + 1, None,
               sum(stocks[code][period]["lines"] for code in remaining_codes),
               sum((stocks[code][period]["amount"] for code in remaining_codes), ZERO))
    output.sort(key=lambda row: (SECTION_ORDER[row["section"]], row["period"], int(row["row_rank"]), row["detail_key"]))
    for section in SECTION_ORDER:
        for period in PERIODS:
            selected_rows = [row for row in output if row["section"] == section and row["period"] == period]
            if sum((Decimal(row["recorded_credits_gbp"]) for row in selected_rows), ZERO) != period_totals[period]["amount"]:
                raise ValueError(f"Remainder amount does not reconcile: {section}.{period}")
            if sum(int(row["credit_line_count"]) for row in selected_rows) != period_totals[period]["lines"]:
                raise ValueError(f"Line-count partition does not reconcile: {section}.{period}")
            if section != "stock_change" and sum(int(row["credit_invoice_count"]) for row in selected_rows) != len(period_totals[period]["invoices"]):
                raise ValueError(f"Invoice-count partition does not reconcile: {section}.{period}")
    monthly = [{"month_number": month,
                "credits_2010_gbp": str(months[f"2010-{month:02d}-01"]),
                "credits_2011_gbp": str(months[f"2011-{month:02d}-01"]),
                "change_gbp": str(months[f"2011-{month:02d}-01"] - months[f"2010-{month:02d}-01"])} for month in range(1, 12)]
    invoice_evidence = [{"period": p, "invoice_no": number, "amount_gbp": str(v["amount"]),
                         "credit_lines": v["lines"], "recorded_dates": sorted(v["dates"]), "stock_codes": sorted(v["codes"])}
                        for (p, number), v in sorted(invoices.items(), key=lambda item: (item[0][0], -item[1]["amount"], item[0][1]))
                        if any(row["section"] == "invoice_concentration" and row["period"] == p and row["detail_key"] == number for row in output)]
    stock_evidence = [{"stock_code": code, "credits_2010_gbp": str(stocks[code][PERIODS[0]]["amount"]),
                      "credits_2011_gbp": str(stocks[code][PERIODS[1]]["amount"]), "change_gbp": str(deltas[code])}
                     for code, _ in selected]
    for period in PERIODS:
        if sum((values[period]["amount"] for values in stocks.values()), ZERO) != period_totals[period]["amount"]:
            raise ValueError("Full stock aggregation does not reconcile")
    findings = {"monthly_contributions": monthly, "invoice_concentration": concentration,
                "top_invoice_evidence": invoice_evidence, "selected_stock_changes": stock_evidence,
                "stock_code_count": len(stocks), "selected_stock_code_count": len(selected),
                "remainder_stock_code_count": len(remaining_codes), "all_remainders_reconcile": True}
    return output, months, findings


def main():
    manifest = json.loads(Path("reports/load_manifest.json").read_text(encoding="utf-8"))
    old = json.loads(Path("reports/identified_sales_local_controls.json").read_text(encoding="utf-8"))
    inputs = dict(old["input_sha256_before_and_after"])
    for filename, required in (("reports/monthly_sales_comparison.json", 220), ("reports/identified_sales_comparison.json", 20)):
        evidence = json.loads(Path(filename).read_text(encoding="utf-8"))
        if evidence["status"] != "passed" or evidence["numeric_comparisons_total"] != required:
            raise ValueError(f"Prerequisite comparison not passed: {filename}")
        for label in ("expected", "observed"):
            info = evidence[label]
            if info["path"] in inputs and inputs[info["path"]] != info["sha256_after"]:
                raise ValueError("Input provenance chain mismatch")
            inputs[info["path"]] = info["sha256_after"]
        inputs[filename] = sha256(Path(filename))
    for name in ("reports/identified_sales_local_controls.json", "src/recorded_credit_analysis.py",
                 "src/compare_recorded_credits.py", "sql/07_recorded_credit_analysis.sql",
                 "data/processed/sql/07_recorded_credit_analysis.sql"):
        inputs[name] = sha256(Path(name))
    for name, digest in inputs.items():
        if sha256(Path(name)) != digest:
            raise ValueError(f"Verified input changed: {name}")
    with localcontext() as context:
        context.prec = 80
        credits, evidence = extract_credits(raw_rows(manifest), load_code_rules(), manifest["null_marker"])
        rows, months, findings = build_outputs(credits)
        for field, expected in (("raw_rows", manifest["raw"]["rows"]),
                                ("excluded_overlap_rows", manifest["overlap_excluded"]["rows"]),
                                ("canonical_rows", manifest["canonical"]["rows"])):
            if evidence[field] != expected:
                raise ValueError(f"Source count differs: {field}")
        if Decimal(evidence["canonical_signed_amount_gbp"]) != Decimal(manifest["canonical"]["signed_amount_gbp"]):
            raise ValueError("Source signed control differs")
        prior_clean = json.loads(Path("reports/cleaning_validation.json").read_text(encoding="utf-8"))
        if evidence["credit_lines_all_history"] != prior_clean["stages"]["recorded_merchandise_credit"]["rows"]:
            raise ValueError("Full-history classification count differs")
        with Path("reports/bigquery_monthly_sales_baseline.csv").open(encoding="utf-8-sig", newline="") as stream:
            baseline = list(csv.DictReader(stream))
        if len(baseline) != 22 or {row["month_start"] for row in baseline} != set(MONTHS):
            raise ValueError("Baseline month keys differ")
        for row in baseline:
            if months[row["month_start"]] != Decimal(row["recorded_merchandise_credits_gbp"]):
                raise ValueError(f"Verified monthly credit total differs: {row['month_start']}")
        totals = {row["period"]: Decimal(row["recorded_credits_gbp"]) for row in rows if row["section"] == "period_summary"}
        if totals != {PERIODS[0]: Decimal("221370.88"), PERIODS[1]: Decimal("286989.56")}:
            raise ValueError("Expected period credit controls differ")
        if sum((Decimal(row["change_gbp"]) for row in findings["monthly_contributions"]), ZERO) != Decimal("65618.68"):
            raise ValueError("Monthly increases do not reconcile")
    for name, digest in inputs.items():
        if sha256(Path(name)) != digest:
            raise ValueError(f"Input changed during calculation: {name}")
    output = Path("reports/recorded_credit_expected.csv")
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    evidence.update({"generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
                     "execution": "Local Python only; recorded-credit GoogleSQL not submitted or dry-run.",
                     "cloud_comparison": "pending", "milestone_3": "in progress",
                     "expected_path": output.as_posix(), "expected_sha256": sha256(output),
                     "expected_rows": len(rows), "expected_columns": COLUMNS,
                     "expected_numeric_cells": sum(row[field] != "" for row in rows for field in COUNT_COLUMNS + MONEY_COLUMNS + RATIO_COLUMNS),
                     "expected_null_cells": sum(row[field] == "" for row in rows for field in COUNT_COLUMNS + MONEY_COLUMNS + RATIO_COLUMNS),
                     "section_rows": {section: sum(row["section"] == section for row in rows) for section in SECTION_ORDER},
                     "input_sha256_before_and_after": inputs, "all_inputs_unchanged": True,
                     "monthly_credit_controls_matched": 22,
                     "findings": findings, "money_method": "Decimal original price text times integer quantity; precision 80; exact sums with no rounding.",
                     "ratio_method": "9 fractional digits, half away from zero, only for means/shares; NULL means undefined or not applicable."})
    Path("reports/recorded_credit_local_controls.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"PASS: {len(rows)} rows; 22 monthly controls match; period amounts and all remainders reconcile. Cloud comparison pending.")


if __name__ == "__main__":
    main()
