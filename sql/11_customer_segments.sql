-- One read-only statement. New segment export comparison pending.
-- Frozen rules: snapshot_segments_v1; historical features only for membership.
-- Base is primary; six fixed sensitivity settings are not competing winners.
-- Outcomes were previously inspected: this is NOT an untouched holdout.
WITH historical_features AS (
  SELECT customer_id,
    MIN(invoice_date) AS first_observed_purchase_date,
    MAX(invoice_date) AS last_observed_purchase_date,
    DATE_DIFF(DATE '2011-08-31', MAX(invoice_date), DAY) AS recency_days,
    COUNT(DISTINCT invoice_no) AS purchase_invoice_count,
    COUNT(DISTINCT invoice_date) AS purchase_day_count,
    SUM(gross_merchandise_amount) AS gross_merchandise_purchase_amount_gbp,
    -- Age since first observation as of cutoff, not last date minus first date.
    DATE_DIFF(DATE '2011-08-31', MIN(invoice_date), DAY) AS observed_tenure_days
  FROM `{{PROJECT_ID}}.{{DATASET_ID}}.purchase_invoices`
  WHERE customer_id IS NOT NULL
    AND invoice_date < DATE '2011-09-01'
  GROUP BY customer_id
), subsequent_purchases AS (
  SELECT customer_id,
    COUNT(DISTINCT invoice_no) AS purchase_invoice_count,
    SUM(gross_merchandise_amount) AS gross_purchase_amount_gbp
  FROM `{{PROJECT_ID}}.{{DATASET_ID}}.purchase_invoices`
  WHERE customer_id IS NOT NULL
    AND invoice_date >= DATE '2011-09-01'
    AND invoice_date < DATE '2011-11-30'
  GROUP BY customer_id
), snapshot AS (
SELECT features.customer_id,
  DATE '2011-08-31' AS feature_cutoff_date,
  features.first_observed_purchase_date,
  features.last_observed_purchase_date,
  features.recency_days,
  features.purchase_invoice_count,
  features.purchase_day_count,
  features.gross_merchandise_purchase_amount_gbp,
  features.observed_tenure_days,
  DATE '2011-09-01' AS outcome_start_date,
  DATE '2011-11-29' AS outcome_end_date,
  COALESCE(outcomes.purchase_invoice_count, 0) > 0 AS subsequent_90d_purchase,
  COALESCE(outcomes.purchase_invoice_count, 0) AS subsequent_90d_purchase_invoice_count,
  COALESCE(outcomes.gross_purchase_amount_gbp, NUMERIC '0')
    AS subsequent_90d_gross_purchase_amount_gbp
FROM historical_features AS features
LEFT JOIN subsequent_purchases AS outcomes USING (customer_id)
), scenario_rules AS (
  SELECT 'base' AS scenario_id, 0 AS scenario_order, 90 AS recency_days, 90 AS recent_tenure_days, 3 AS core_purchase_days, NUMERIC '2000' AS core_gross_gbp
  UNION ALL
  SELECT 'recency_75' AS scenario_id, 1 AS scenario_order, 75 AS recency_days, 90 AS recent_tenure_days, 3 AS core_purchase_days, NUMERIC '2000' AS core_gross_gbp
  UNION ALL
  SELECT 'recency_105' AS scenario_id, 2 AS scenario_order, 105 AS recency_days, 90 AS recent_tenure_days, 3 AS core_purchase_days, NUMERIC '2000' AS core_gross_gbp
  UNION ALL
  SELECT 'tenure_75' AS scenario_id, 3 AS scenario_order, 90 AS recency_days, 75 AS recent_tenure_days, 3 AS core_purchase_days, NUMERIC '2000' AS core_gross_gbp
  UNION ALL
  SELECT 'tenure_105' AS scenario_id, 4 AS scenario_order, 90 AS recency_days, 105 AS recent_tenure_days, 3 AS core_purchase_days, NUMERIC '2000' AS core_gross_gbp
  UNION ALL
  SELECT 'gross_1500' AS scenario_id, 5 AS scenario_order, 90 AS recency_days, 90 AS recent_tenure_days, 3 AS core_purchase_days, NUMERIC '1500' AS core_gross_gbp
  UNION ALL
  SELECT 'gross_2500' AS scenario_id, 6 AS scenario_order, 90 AS recency_days, 90 AS recent_tenure_days, 3 AS core_purchase_days, NUMERIC '2500' AS core_gross_gbp
), segment_definitions AS (
  SELECT 'recently_first_observed' AS segment_id, 1 AS segment_order
  UNION ALL
  SELECT 'recent_established_higher_spend_repeat' AS segment_id, 2 AS segment_order
  UNION ALL
  SELECT 'other_recent_established' AS segment_id, 3 AS segment_order
  UNION ALL
  SELECT 'less_recent_multi_day' AS segment_id, 4 AS segment_order
  UNION ALL
  SELECT 'less_recent_single_day' AS segment_id, 5 AS segment_order
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
