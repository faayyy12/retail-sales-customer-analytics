"""Frozen historical segmentation and local outcome controls; no cloud access."""

from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
import csv
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from cohort_purchase_timing import check_hashes
from compare_category_controls import sha256
from compare_customer_snapshot import read_csv as read_snapshot
from customer_snapshot import BOOL_COLUMN, COLUMNS as SNAPSHOT_COLUMNS, row_errors, typed
from monthly_sales_baseline import ratio


RULES_PATH = Path("docs/customer_segment_rules.json")
FROZEN_RULES_SHA256 = "d64154efd3be92ea6f3bf222675dfbca70f7b47dfbd2198c7af3211e8972810c"
FEATURES = ["observed_tenure_days", "recency_days", "purchase_day_count", "gross_merchandise_purchase_amount_gbp"]
KEYS = ["scenario_id", "segment_id"]
COUNT_COLUMNS = ["customer_count", "subsequent_purchasing_customers", "subsequent_invoice_count"]
MONEY_COLUMN = "subsequent_gross_purchases_gbp"
RATIO_COLUMNS = ["customer_share", "subsequent_purchase_rate", "subsequent_gross_purchases_per_customer_gbp",
                 "subsequent_gross_purchases_share"]
FLAG_COLUMN = "small_customer_denominator"
COLUMNS = KEYS + ["customer_count", "customer_share", "subsequent_purchasing_customers", "subsequent_purchase_rate",
                  "subsequent_invoice_count", MONEY_COLUMN, "subsequent_gross_purchases_per_customer_gbp",
                  "subsequent_gross_purchases_share", FLAG_COLUMN]
ZERO = Decimal(0)


def load_rules():
    if sha256(RULES_PATH) != FROZEN_RULES_SHA256:
        raise ValueError("Frozen rules changed; do not tune them after outcome evaluation")
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def assign(features, thresholds):
    """No outcome argument/access. Evaluate the mutually exclusive rules in order."""
    tenure, recency, days, gross = [Decimal(str(features[field])) for field in FEATURES]
    if tenure <= thresholds["recent_tenure_days"]:
        return "recently_first_observed"
    if recency <= thresholds["recency_days"]:
        if days >= thresholds["core_purchase_days"] and gross >= Decimal(thresholds["core_gross_gbp"]):
            return "recent_established_higher_spend_repeat"
        return "other_recent_established"
    return "less_recent_multi_day" if days >= 2 else "less_recent_single_day"


def historical_profile(rows):
    """Reproduce the pre-freeze feature evidence without accessing outcome fields."""
    fields = ["recency_days", "purchase_invoice_count", "purchase_day_count",
              "gross_merchandise_purchase_amount_gbp", "observed_tenure_days"]
    features = [{field: Decimal(row[field]) for field in fields} for row in rows]
    def distribution(values):
        ordered = sorted(values)
        result = {"n": len(values), "min": str(ordered[0])}
        for text in (".1", ".25", ".5", ".75", ".9", ".95"):
            p = Decimal(text)
            index = (len(values) - 1) * p
            low, weight = int(index), index - int(index)
            value = ordered[low] + weight * (ordered[min(low + 1, len(values) - 1)] - ordered[low])
            result[f"p{int(p * 100):02d}"] = str(value)
        result["max"] = str(ordered[-1])
        return result
    with localcontext() as context:
        context.prec = 80
        recent = [row for row in features if row["observed_tenure_days"] > 90 and row["recency_days"] <= 90]
        return {"distributions": {field: distribution([row[field] for row in features]) for field in fields},
                "historical_counts": {**{f"tenure_le_{n}": sum(row["observed_tenure_days"] <= n for row in features) for n in (60, 90, 120)},
                                      "recency_le_90": sum(row["recency_days"] <= 90 for row in features),
                                      "single_purchase_day": sum(row["purchase_day_count"] == 1 for row in features)},
                "established_recent_distributions_tenure_gt90_recency_le90": {
                    field: distribution([row[field] for row in recent]) for field in ("purchase_day_count", "gross_merchandise_purchase_amount_gbp")}}


def aggregate(rows, rules):
    """All customers appear once per scenario; empty groups remain visible."""
    if len({row["customer_id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate customer input")
    with localcontext() as context:
        context.prec = 80
        total_gross = sum((Decimal(row["subsequent_90d_gross_purchase_amount_gbp"]) for row in rows), ZERO)
        output, assignments, historical = [], {}, {}
        for scenario in rules["scenarios"]:
            by_group = defaultdict(list)
            labels = {}
            # Freeze assignments using projected historical fields before summing outcomes.
            for row in rows:
                features = {field: row[field] for field in FEATURES}
                label = assign(features, scenario["thresholds"])
                labels[row["customer_id"]] = label
                by_group[label].append(row)
            assignments[scenario["id"]] = labels
            historical[scenario["id"]] = {}
            for segment in rules["segments"]:
                selected = by_group[segment["id"]]
                n = len(selected)
                purchasing = sum(row[BOOL_COLUMN].lower() == "true" for row in selected)
                invoices = sum(int(row["subsequent_90d_purchase_invoice_count"]) for row in selected)
                gross = sum((Decimal(row["subsequent_90d_gross_purchase_amount_gbp"]) for row in selected), ZERO)
                values = {"scenario_id": scenario["id"], "segment_id": segment["id"], "customer_count": n,
                          "customer_share": ratio(n, len(rows)), "subsequent_purchasing_customers": purchasing,
                          "subsequent_purchase_rate": ratio(purchasing, n), "subsequent_invoice_count": invoices,
                          MONEY_COLUMN: gross, "subsequent_gross_purchases_per_customer_gbp": ratio(gross, n),
                          "subsequent_gross_purchases_share": ratio(gross, total_gross),
                          FLAG_COLUMN: n < rules["small_group_threshold"]}
                output.append({field: "" if values[field] is None else str(values[field]).lower()
                               if isinstance(values[field], bool) else str(values[field]) for field in COLUMNS})
                historical[scenario["id"]][segment["id"]] = {
                    "customer_count": n,
                    "purchase_invoices": sum(int(row["purchase_invoice_count"]) for row in selected),
                    "purchase_days": sum(int(row["purchase_day_count"]) for row in selected),
                    "gross_gbp": str(sum((Decimal(row["gross_merchandise_purchase_amount_gbp"]) for row in selected), ZERO))}
            if sum(len(group) for group in by_group.values()) != len(rows):
                raise ValueError("Groups do not exhaust customer population")
        return output, assignments, historical


def scenario_controls(rows):
    controls = {}
    for scenario in sorted({row["scenario_id"] for row in rows}):
        selected = [row for row in rows if row["scenario_id"] == scenario]
        with localcontext() as context:
            context.prec = 80
            controls[scenario] = {field: sum(int(row[field]) for row in selected) for field in COUNT_COLUMNS}
            controls[scenario][MONEY_COLUMN] = format(sum((Decimal(row[MONEY_COLUMN]) for row in selected), ZERO).normalize(), "f")
    return controls


def load_snapshot(path):
    info, records = read_snapshot(path)
    if (not info["schema_matches"] or info["duplicate_customer_ids"] or info["invalid_customer_key_rows"]
            or info["malformed_records"]):
        raise ValueError(f"Invalid snapshot: {path}")
    for customer, row in records.items():
        errors = row_errors({field: typed(row[field], field) for field in SNAPSHOT_COLUMNS[1:]})
        if errors:
            raise ValueError(f"Invalid verified snapshot row: {customer}: {errors}")
    return list(records.values())


def sql_template(rules):
    """Inline verified snapshot logic; its read-only result is not a stored table."""
    source = Path("sql/10_customer_snapshot.sql").read_text(encoding="utf-8")
    source = source[source.index("WITH historical_features AS"):]
    prefix, projection = source.split("\nSELECT features.customer_id,", 1)
    projection = "SELECT features.customer_id," + projection.split("\nORDER BY features.customer_id;", 1)[0]
    scenarios = []
    for order, scenario in enumerate(rules["scenarios"]):
        t = scenario["thresholds"]
        scenarios.append(f"  SELECT '{scenario['id']}' AS scenario_id, {order} AS scenario_order, "
                         f"{t['recency_days']} AS recency_days, {t['recent_tenure_days']} AS recent_tenure_days, "
                         f"{t['core_purchase_days']} AS core_purchase_days, NUMERIC '{t['core_gross_gbp']}' AS core_gross_gbp")
    groups = [f"  SELECT '{segment['id']}' AS segment_id, {segment['order']} AS segment_order" for segment in rules["segments"]]
    return ("-- One read-only statement. New segment export comparison pending.\n"
            "-- Frozen rules: snapshot_segments_v1; historical features only for membership.\n"
            "-- Base is primary; six fixed sensitivity settings are not competing winners.\n"
            "-- Outcomes were previously inspected: this is NOT an untouched holdout.\n" + prefix + ", snapshot AS (\n" + projection +
            "\n), scenario_rules AS (\n" + "\n  UNION ALL\n".join(scenarios) +
            "\n), segment_definitions AS (\n" + "\n  UNION ALL\n".join(groups) + """
), assigned AS (
  SELECT settings.scenario_id,
    CASE
      WHEN snapshot.observed_tenure_days <= settings.recent_tenure_days
        THEN 'recently_first_observed'
      WHEN snapshot.recency_days <= settings.recency_days
        AND snapshot.purchase_day_count >= settings.core_purchase_days
        AND snapshot.gross_merchandise_purchase_amount_gbp >= settings.core_gross_gbp
        THEN 'recent_established_higher_spend_repeat'
      WHEN snapshot.recency_days <= settings.recency_days THEN 'other_recent_established'
      WHEN snapshot.purchase_day_count >= 2 THEN 'less_recent_multi_day'
      ELSE 'less_recent_single_day'
    END AS segment_id,
    snapshot.subsequent_90d_purchase, snapshot.subsequent_90d_purchase_invoice_count,
    snapshot.subsequent_90d_gross_purchase_amount_gbp
  FROM snapshot CROSS JOIN scenario_rules AS settings
), segment_counts AS (
  SELECT scenario_id, segment_id, COUNT(*) AS customer_count,
    COUNTIF(subsequent_90d_purchase) AS subsequent_purchasing_customers,
    SUM(subsequent_90d_purchase_invoice_count) AS subsequent_invoice_count,
    SUM(subsequent_90d_gross_purchase_amount_gbp) AS subsequent_gross_purchases_gbp
  FROM assigned
  GROUP BY scenario_id, segment_id
), scenario_totals AS (
  SELECT scenario_id, SUM(customer_count) AS total_customers,
    SUM(subsequent_gross_purchases_gbp) AS total_gross
  FROM segment_counts
  GROUP BY scenario_id
)
SELECT settings.scenario_id, segment_defs.segment_id,
  COALESCE(counts.customer_count, 0) AS customer_count,
  CAST(ROUND(SAFE_DIVIDE(CAST(COALESCE(counts.customer_count, 0) AS BIGNUMERIC),
    CAST(totals.total_customers AS BIGNUMERIC)), 9) AS NUMERIC) AS customer_share,
  COALESCE(counts.subsequent_purchasing_customers, 0) AS subsequent_purchasing_customers,
  CAST(ROUND(SAFE_DIVIDE(CAST(COALESCE(counts.subsequent_purchasing_customers, 0) AS BIGNUMERIC),
    CAST(COALESCE(counts.customer_count, 0) AS BIGNUMERIC)), 9) AS NUMERIC) AS subsequent_purchase_rate,
  COALESCE(counts.subsequent_invoice_count, 0) AS subsequent_invoice_count,
  COALESCE(counts.subsequent_gross_purchases_gbp, NUMERIC '0') AS subsequent_gross_purchases_gbp,
  CAST(ROUND(SAFE_DIVIDE(CAST(COALESCE(counts.subsequent_gross_purchases_gbp, NUMERIC '0') AS BIGNUMERIC),
    CAST(COALESCE(counts.customer_count, 0) AS BIGNUMERIC)), 9) AS NUMERIC)
    AS subsequent_gross_purchases_per_customer_gbp,
  CAST(ROUND(SAFE_DIVIDE(CAST(COALESCE(counts.subsequent_gross_purchases_gbp, NUMERIC '0') AS BIGNUMERIC),
    CAST(totals.total_gross AS BIGNUMERIC)), 9) AS NUMERIC) AS subsequent_gross_purchases_share,
  COALESCE(counts.customer_count, 0) < 30 AS small_customer_denominator
FROM scenario_rules AS settings CROSS JOIN segment_definitions AS segment_defs
LEFT JOIN segment_counts AS counts USING (scenario_id, segment_id)
JOIN scenario_totals AS totals USING (scenario_id)
ORDER BY settings.scenario_order, segment_defs.segment_order;
""")


def main():
    rules = load_rules()  # Frozen artifact validated before reading any group outcomes.
    proof_path = Path("reports/customer_snapshot_comparison.json")
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    if proof["status"] != "passed" or proof["field_comparisons"] != 67912 or not proof["preparation_controls_match"]:
        raise ValueError("Snapshot comparison must pass first")
    inputs = dict(proof["input_sha256_before_and_after"])
    inputs[proof_path.as_posix()] = sha256(proof_path)
    for side in ("expected", "observed"):
        inputs[proof[side]["path"]] = proof[side]["sha256_after"]
    inputs[RULES_PATH.as_posix()] = FROZEN_RULES_SHA256
    inputs[rules["feature_profile_path"]] = rules["feature_profile_sha256"]
    for name in ("src/customer_segments.py", "src/compare_customer_segments.py", "sql/11_customer_segments.sql",
                 "data/processed/sql/11_customer_segments.sql"):
        inputs[name] = sha256(Path(name))
    check_hashes(inputs)
    if Path("sql/11_customer_segments.sql").read_text(encoding="utf-8") != sql_template(rules):
        raise ValueError("SQL differs from frozen rules and verified snapshot logic")
    rows = load_snapshot(Path(proof["expected"]["path"]))
    profile = json.loads(Path(rules["feature_profile_path"]).read_text(encoding="utf-8"))
    if any(profile[field] != value for field, value in historical_profile(rows).items()):
        raise ValueError("Historical-only distribution evidence differs from frozen profile")
    results, assignments, historical = aggregate(rows, rules)
    observed_results, observed_assignments, _ = aggregate(load_snapshot(Path(proof["observed"]["path"])), rules)
    # Decimal display scale is not part of exact numeric agreement.
    def semantic_output(records):
        return [{field: Decimal(row[field]) if field in COUNT_COLUMNS + RATIO_COLUMNS + [MONEY_COLUMN]
                 and row[field] != "" else row[field] for field in COLUMNS} for row in records]
    if semantic_output(results) != semantic_output(observed_results) or assignments != observed_assignments:
        raise ValueError("Local and verified BigQuery snapshots give different segmentation results")
    controls = scenario_controls(results)
    for scenario in rules["scenarios"]:
        expected = proof["summary_controls"]["expected"]
        required = {"customer_count": expected["customer_rows"], "subsequent_purchasing_customers": expected["customers_with_subsequent_purchase"],
                    "subsequent_invoice_count": expected["subsequent_purchase_invoices"], MONEY_COLUMN: expected["subsequent_gross_purchase_amount_gbp"]}
        if controls[scenario["id"]] != required:
            raise ValueError(f"Scenario does not reconcile: {scenario['id']}")
        if {group: values["customer_count"] for group, values in historical[scenario["id"]].items()} != rules["historical_only_group_sizes"][scenario["id"]]:
            raise ValueError("Historical-only frozen group sizes differ")
        h = historical[scenario["id"]]
        if (sum(v["purchase_invoices"] for v in h.values()) != 29534 or sum(v["purchase_days"] for v in h.values()) != 26520
                or sum((Decimal(v["gross_gbp"]) for v in h.values()), ZERO) != Decimal("13523021.07")):
            raise ValueError("Historical group controls differ from snapshot totals")
    transitions = {}
    for scenario, labels in assignments.items():
        changes = Counter((assignments["base"][customer], group) for customer, group in labels.items()
                          if group != assignments["base"][customer])
        transitions[scenario] = {"moved_customers_from_base": sum(changes.values()),
                                 "transitions": [{"from": left, "to": right, "customers": n} for (left, right), n in sorted(changes.items())]}
    check_hashes(inputs)
    output = Path("reports/customer_segments_expected.csv")
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(results)
    evidence = {"generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(), "milestone_5": "in progress",
                "new_segment_export_comparison": "pending", "execution": "Local aggregation of source-backed and verified snapshot CSVs, independently cross-checked. No cloud query/dry run.",
                "rules_version": rules["version"], "rules_frozen_at": rules["frozen_at"], "frozen_rules_sha256": FROZEN_RULES_SHA256,
                "prior_outcome_exposure": rules["outcome_exposure"], "thresholds_reselected_after_evaluation": False,
                "output_path": output.as_posix(), "output_sha256": sha256(output), "output_bytes": output.stat().st_size,
                "expected_columns": COLUMNS, "expected_rows": len(results),
                "expected_nonnull_field_cells": sum(row[field] != "" for row in results for field in COLUMNS[2:]),
                "expected_null_field_cells": sum(row[field] == "" for row in results for field in COLUMNS[2:]),
                "input_sha256_before_and_after": inputs, "all_inputs_unchanged": True,
                "scenario_controls": controls, "historical_segment_controls": historical, "sensitivity_membership_changes": transitions,
                "all_groups_mutually_exclusive_and_exhaustive": True, "verified_snapshot_aggregation_matches": True,
                "pre_freeze_historical_distributions_reproduced": True,
                "ratio_precision": "9 decimal places, half away from zero; exact unrounded money sums. Empty-group purchase rate/per-customer gross NULL.",
                "cloud_job_metadata": "Unconfirmed; new segment SQL not executed or dry-run."}
    Path("reports/customer_segments_local_controls.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"PASS: {len(results)} rows x {len(COLUMNS)} columns; all seven scenarios reconcile. New cloud comparison pending.")
    for row in results:
        if row["scenario_id"] == "base":
            print(json.dumps(row))


if __name__ == "__main__":
    main()
