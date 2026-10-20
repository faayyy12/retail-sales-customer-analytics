"""Small Tableau inputs from verified exports; no source rebuild or cloud access."""

import argparse
import csv
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path

from compare_category_controls import sha256
from monthly_sales_baseline import ratio


PROOFS = ["monthly_sales_comparison", "identified_sales_comparison",
          "cohort_purchase_timing_comparison", "customer_segments_comparison"]
PERIOD_COLUMNS = [
    "year", "period_start", "period_end", "gross_merchandise_sales_gbp",
    "recorded_merchandise_credits_gbp", "net_recorded_merchandise_sales_gbp",
    "purchase_invoice_count", "average_purchase_invoice_value_gbp",
    "identified_purchasing_customers", "identified_purchase_invoice_count",
    "anonymous_purchase_invoice_count", "identified_gross_merchandise_sales_gbp",
    "anonymous_gross_merchandise_sales_gbp", "identified_gross_sales_share",
]
MANIFEST = Path("reports/presentation_data_controls.json")


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        if any(None in row or any(v is None for v in row.values()) for row in rows):
            raise ValueError(f"Malformed CSV: {path}")
        return reader.fieldnames, rows


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked_inputs():
    """Reuse successful comparisons, checking both CSVs and recorded provenance."""
    hashes, observed = {}, {}
    for name in PROOFS:
        path = Path("reports") / f"{name}.json"
        proof = json.loads(path.read_text(encoding="utf-8"))
        require(proof["status"] == "passed", f"Comparison not passed: {path}")
        hashes[path.as_posix()] = sha256(path)
        for label in ("expected", "observed"):
            info = proof[label]
            require(info["unchanged"] and info["sha256_before"] == info["sha256_after"],
                    f"CSV changed during original comparison: {info['path']}")
            hashes[info["path"]] = info["sha256_after"]
        hashes.update(proof.get("input_sha256_before_and_after", {}))
        observed[name] = proof["observed"]["path"]
    check_hashes(hashes)
    return hashes, observed


def check_hashes(hashes):
    for name, expected in hashes.items():
        require(sha256(Path(name)) == expected, f"Input hash mismatch: {name}")


def total(rows, field):
    return sum((Decimal(row[field]) for row in rows), Decimal(0))


def period_summary(monthly, identified):
    months = {f"{year}-{month:02d}-01" for year in (2010, 2011) for month in range(1, 12)}
    require(len(monthly) == 22 and {r["month_start"] for r in monthly} == months,
            "Monthly keys must cover exactly January-November in both years")
    periods = {row["period"]: row for row in identified}
    require(len(identified) == 2 and set(periods) == {"2010_jan_nov", "2011_jan_nov"},
            "Expected two unique full-period identified controls")
    result = []
    for year in (2010, 2011):
        rows = [r for r in monthly if r["month_start"].startswith(str(year))]
        control = periods[f"{year}_jan_nov"]
        summed = {field: total(rows, field) for field in [
            "gross_merchandise_sales_gbp", "recorded_merchandise_credits_gbp",
            "net_recorded_merchandise_sales_gbp", "purchase_invoice_count",
            "identified_purchase_invoice_count", "anonymous_purchase_invoice_count",
            "identified_gross_merchandise_sales_gbp"]}
        gross = summed["gross_merchandise_sales_gbp"]
        identified_gross = summed["identified_gross_merchandise_sales_gbp"]
        require(gross == Decimal(control["total_gross_merchandise_sales_gbp"]), "Gross control")
        require(summed["purchase_invoice_count"] == Decimal(control["total_purchase_invoice_count"]),
                "Invoice control")
        for field in ("identified_gross_merchandise_sales_gbp", "identified_purchase_invoice_count",
                      "anonymous_purchase_invoice_count"):
            require(summed[field] == Decimal(control[field]), f"Identified control: {field}")
        require(gross - identified_gross == Decimal(control["anonymous_gross_merchandise_sales_gbp"]),
                "Anonymous gross control")
        require(gross - summed["recorded_merchandise_credits_gbp"] ==
                summed["net_recorded_merchandise_sales_gbp"], "Gross less credits identity")
        require(summed["identified_purchase_invoice_count"] + summed["anonymous_purchase_invoice_count"]
                == summed["purchase_invoice_count"], "Invoice population partition")
        coverage = ratio(identified_gross, gross)
        require(coverage == Decimal(control["identified_gross_sales_share"]), "Coverage control")
        result.append({"year": year, "period_start": f"{year}-01-01", "period_end": f"{year}-11-30",
                       **summed,
                       "average_purchase_invoice_value_gbp": ratio(gross, summed["purchase_invoice_count"]),
                       # Full-period distinct counts: NEVER sum monthly distinct counts.
                       "identified_purchasing_customers": int(control["identified_purchasing_customers"]),
                       "anonymous_gross_merchandise_sales_gbp": gross - identified_gross,
                       "identified_gross_sales_share": coverage})
    return result


def segment_partitions(rows):
    scenarios = {"base", "recency_75", "recency_105", "tenure_75", "tenure_105",
                 "gross_1500", "gross_2500"}
    require(len(rows) == 35 and len({(r["scenario_id"], r["segment_id"]) for r in rows}) == 35,
            "Expected 35 unique segment keys")
    require({r["scenario_id"] for r in rows} == scenarios, "Scenario keys")
    for scenario in scenarios:
        subset = [r for r in rows if r["scenario_id"] == scenario]
        require(len(subset) == 5, "Five groups per scenario")
        for field, expected in [("customer_count", 5224), ("subsequent_purchasing_customers", 2236),
                                ("subsequent_invoice_count", 5207),
                                ("subsequent_gross_purchases_gbp", "2699494.19")]:
            require(total(subset, field) == Decimal(expected), f"Scenario total: {scenario}/{field}")
    return ([r for r in rows if r["scenario_id"] == "base"],
            [r for r in rows if r["scenario_id"] != "base"])


def cohort_checks(rows):
    require(len(rows) == 24 and len({r["cohort_month"] for r in rows}) == 24, "Cohort keys")
    for field, expected in [("total_customers", 5824), ("eligible_customers", 5227),
                            ("ineligible_customers", 597), ("eligible_repeaters_30d", 974),
                            ("eligible_repeaters_60d", 1791), ("eligible_repeaters_90d", 2305),
                            ("eligible_nonrepeaters_90d", 2922)]:
        require(total(rows, field) == expected, f"Cohort total: {field}")
    for row in rows:
        for days in (30, 60, 90):
            expected = ratio(int(row[f"eligible_repeaters_{days}d"]), int(row["eligible_customers"]))
            require((row[f"repeat_rate_{days}d"] == "" if expected is None else
                     Decimal(row[f"repeat_rate_{days}d"]) == expected), "Cohort rate/NULL semantics")


def csv_bytes(columns, rows):
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify prepared files without rewriting evidence")
    args = parser.parse_args()
    hashes, observed = checked_inputs()
    _, monthly = read_csv(observed["monthly_sales_comparison"])
    _, identified = read_csv(observed["identified_sales_comparison"])
    segment_columns, segments = read_csv(observed["customer_segments_comparison"])
    _, cohorts = read_csv(observed["cohort_purchase_timing_comparison"])
    periods = period_summary(monthly, identified)
    base, sensitivity = segment_partitions(segments)
    cohort_checks(cohorts)
    outputs = [
        ("exports/tableau_sales_periods.csv", PERIOD_COLUMNS, periods,
         "One full January-November period per year. Sum additive monthly metrics; obtain full-period distinct customers from the identified-sales export; recompute ratios from totals."),
        ("exports/tableau_customer_segments_base.csv", segment_columns, base,
         "One frozen base segment; exact row selection scenario_id=base, with all source fields preserved."),
        ("exports/tableau_customer_segments_sensitivity.csv", segment_columns, sensitivity,
         "One scenario/segment for six non-base alternatives; exact row selection scenario_id!=base, preserving all fields. Never sum across scenarios."),
    ]
    details = []
    for name, columns, rows, transformation in outputs:
        path = Path(name)
        content = csv_bytes(columns, rows)
        if args.check:
            require(path.read_bytes() == content, f"Prepared output differs: {path}")
        else:
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(content)
        details.append({"path": name, "sha256": sha256(path), "rows": len(rows),
                        "columns": len(columns), "transformation": transformation})
    check_hashes(hashes)
    evidence = {"status": "passed", "milestone_6": "in progress; Tableau not authored or verified",
                "execution": "Local presentation-only aggregation/selection from unchanged verified exports; no cloud action.",
                "input_sha256_before_and_after": hashes, "outputs": details,
                "direct_connections": [observed["monthly_sales_comparison"], observed["cohort_purchase_timing_comparison"]],
                "sales_period_controls": [{k: str(v) for k, v in row.items()} for row in periods],
                "cohort_controls": {f: str(total(cohorts, f)) for f in ["total_customers", "eligible_customers", "ineligible_customers", "eligible_repeaters_30d", "eligible_repeaters_60d", "eligible_repeaters_90d", "eligible_nonrepeaters_90d"]},
                "checks": {"full_period_customers_not_monthly_sum": True, "exact_decimal_amounts": True,
                           "period_totals_match_verified_identified_controls": True,
                           "rates_and_averages_recomputed_from_totals": True,
                           "base_and_sensitivity_disjoint_and_cover_source": True,
                           "every_scenario_reconciles_to_snapshot": True,
                           "cohort_counts_rates_and_nulls_preserved": True,
                           "all_recorded_inputs_unchanged": True},
                "cloud_job_metadata": "Unavailable metadata remains unconfirmed; no new cloud job.",
                "tableau_checks": "Pending manual authoring, rendered values, NULL display, filter behavior and dashboard acceptance."}
    if args.check:
        require(json.loads(MANIFEST.read_text(encoding="utf-8")) == evidence, "Presentation manifest differs")
    else:
        MANIFEST.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"PASSED: 2 sales periods, 5 base segments, 30 sensitivity rows; 22 monthly and 24 cohort rows reused. {'Checked' if args.check else 'Prepared'} locally; Tableau checks pending.")


if __name__ == "__main__":
    main()
