"""Describe reconciled monthly sales locally; no cloud access or causal claims."""

from datetime import datetime
from decimal import Decimal, localcontext
import csv
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from compare_category_controls import sha256
from monthly_sales_baseline import MONTHS, ratio


ADDITIVE_FIELDS = ["gross_merchandise_sales_gbp", "recorded_merchandise_credits_gbp",
                   "net_recorded_merchandise_sales_gbp", "purchase_invoice_count",
                   "identified_purchase_invoice_count", "anonymous_purchase_invoice_count",
                   "identified_gross_merchandise_sales_gbp"]


def summarize(rows):
    if len(rows) != 22 or sorted(row["month_start"] for row in rows) != MONTHS:
        raise ValueError("Expected the 22 unique comparison months")
    with localcontext() as context:
        context.prec = 80
        periods = {}
        for year in ("2010", "2011"):
            selected = [row for row in rows if row["month_start"].startswith(year)]
            totals = {field: sum((Decimal(row[field]) for row in selected), Decimal(0))
                      for field in ADDITIVE_FIELDS}
            totals["anonymous_gross_merchandise_sales_gbp"] = (
                totals["gross_merchandise_sales_gbp"] - totals["identified_gross_merchandise_sales_gbp"])
            periods[year] = totals
        changes = {}
        for field in periods["2010"]:
            earlier, later = periods["2010"][field], periods["2011"][field]
            changes[field] = {"absolute": str(later - earlier),
                              "percent": str(ratio(100 * (later - earlier), earlier))}
        ratios = {
            "average_purchase_invoice_value_gbp": ("gross_merchandise_sales_gbp", "purchase_invoice_count", Decimal(1)),
            "identified_gross_sales_share_percent": ("identified_gross_merchandise_sales_gbp", "gross_merchandise_sales_gbp", Decimal(100)),
            "recorded_credits_as_percent_of_gross": ("recorded_merchandise_credits_gbp", "gross_merchandise_sales_gbp", Decimal(100)),
        }
        for field, (numerator, denominator, multiplier) in ratios.items():
            a = multiplier * periods["2010"][numerator] / periods["2010"][denominator]
            b = multiplier * periods["2011"][numerator] / periods["2011"][denominator]
            for year in ("2010", "2011"):
                periods[year][field] = ratio(multiplier * periods[year][numerator], periods[year][denominator])
            changes[field] = {"absolute": str(ratio(b - a, 1)),
                              "absolute_unit": "percentage points" if multiplier == 100 else "GBP",
                              "percent": str(ratio(100 * (b - a), a))}
        monthly = []
        keyed = {row["month_start"]: row for row in rows}
        for month in range(1, 12):
            earlier, later = keyed[f"2010-{month:02d}-01"], keyed[f"2011-{month:02d}-01"]
            a, b = Decimal(earlier["gross_merchandise_sales_gbp"]), Decimal(later["gross_merchandise_sales_gbp"])
            monthly.append({"month_number": month, "gross_sales_change_gbp": str(b - a),
                            "gross_sales_change_percent": str(ratio(100 * (b - a), a))})
        if (Decimal(changes["identified_gross_merchandise_sales_gbp"]["absolute"])
                + Decimal(changes["anonymous_gross_merchandise_sales_gbp"]["absolute"])
                != Decimal(changes["gross_merchandise_sales_gbp"]["absolute"])):
            raise ValueError("Identified/anonymous change reconciliation failed")
        if (Decimal(changes["gross_merchandise_sales_gbp"]["absolute"])
                - Decimal(changes["recorded_merchandise_credits_gbp"]["absolute"])
                != Decimal(changes["net_recorded_merchandise_sales_gbp"]["absolute"])):
            raise ValueError("Gross/credit/net change reconciliation failed")
    return {"periods": {year: {key: str(value) for key, value in values.items()}
                        for year, values in periods.items()},
            "changes_2011_vs_2010": changes, "monthly_gross_sales_changes": monthly}


def main():
    comparison_path = Path("reports/monthly_sales_comparison.json")
    comparison_hash = sha256(comparison_path)
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    if comparison["status"] != "passed" or comparison["numeric_comparisons_total"] != 220:
        raise ValueError("Monthly reconciliation must pass before describing results")
    for label in ("expected", "observed"):
        info = comparison[label]
        if sha256(Path(info["path"])) != info["sha256_after"]:
            raise ValueError(f"Reconciled input changed: {label}")
    observed = Path(comparison["observed"]["path"])
    with observed.open(encoding="utf-8-sig", newline="") as stream:
        result = summarize(list(csv.DictReader(stream)))
    result.update({
        "generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
        "observed_path": observed.as_posix(), "observed_sha256": comparison["observed"]["sha256_after"],
        "comparison_path": comparison_path.as_posix(), "comparison_sha256": comparison_hash,
        "method": "Sum additive metrics, then recompute period ratios from total numerators/denominators. Decimal precision 80; ratios/percent changes serialized to 9 decimals, half away from zero. Presentation rounding only in prose/tables.",
        "limitations": "Do not sum monthly distinct customers or average monthly ratios. Anonymous sales are an ID-coverage population, not a customer segment. Recorded credits are assigned to their recorded dates, not original purchases. No causal conclusions or profit claims.",
        "cloud_job_metadata": "Not supplied; job ID, timing, processing and byte settings remain unconfirmed.",
        "milestone_3": "In progress; monthly baseline reconciled, remaining sales-driver analysis pending.",
    })
    for label in ("expected", "observed"):
        info = comparison[label]
        if sha256(Path(info["path"])) != info["sha256_after"]:
            raise ValueError(f"Input changed during summary: {label}")
    if sha256(comparison_path) != comparison_hash:
        raise ValueError("Comparison evidence changed during summary")
    Path("reports/monthly_sales_summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("PASS: reconciled monthly findings summarized; both CSVs and comparison evidence unchanged.")


if __name__ == "__main__":
    main()
