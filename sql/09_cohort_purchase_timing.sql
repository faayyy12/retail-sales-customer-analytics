-- One read-only GoogleSQL statement; new cohort export comparison pending.
-- Reuses the verified 08 history CTEs exactly, including full earlier history.
-- All 30/60/90-day rates use the SAME fully 90-day-eligible denominator.
-- First-observed cohorts are not acquisition cohorts; source dates stay naive.
WITH customer_purchase_days AS (
  -- Different invoices on the same day cannot by themselves establish a repeat.
  SELECT customer_id, invoice_date AS purchase_date
  FROM `{{PROJECT_ID}}.{{DATASET_ID}}.purchase_invoices`
  WHERE customer_id IS NOT NULL
    AND invoice_date < DATE '2011-12-01'
  GROUP BY customer_id, purchase_date
), ordered_days AS (
  SELECT customer_id, purchase_date,
    LEAD(purchase_date) OVER (
      PARTITION BY customer_id ORDER BY purchase_date) AS next_purchase_date,
    ROW_NUMBER() OVER (
      PARTITION BY customer_id ORDER BY purchase_date) AS purchase_day_number
  FROM customer_purchase_days
), first_purchases AS (
  SELECT customer_id,
    purchase_date AS first_observed_purchase_date,
    next_purchase_date AS first_later_day_purchase_date
  FROM ordered_days
  WHERE purchase_day_number = 1
), histories AS (
  SELECT customer_id, first_observed_purchase_date,
    DATE_TRUNC(first_observed_purchase_date, MONTH) AS cohort_month,
    first_later_day_purchase_date,
    DATE_DIFF(first_later_day_purchase_date, first_observed_purchase_date, DAY)
      AS days_to_first_later_day_purchase,
    DATE_ADD(first_observed_purchase_date, INTERVAL 90 DAY) <= DATE '2011-11-30'
      AS has_full_90_day_followup
  FROM first_purchases
), customer_histories AS (
  SELECT customer_id, cohort_month, has_full_90_day_followup,
    days_to_first_later_day_purchase,
    CASE
      WHEN NOT has_full_90_day_followup THEN CAST(NULL AS BOOL)
      ELSE COALESCE(days_to_first_later_day_purchase BETWEEN 1 AND 90, FALSE)
    END AS repeat_purchase_within_90_days
  FROM histories
), cohort_calendar AS (
  SELECT cohort_month
  FROM UNNEST(GENERATE_DATE_ARRAY(
    DATE '2009-12-01', DATE '2011-11-01', INTERVAL 1 MONTH)) AS cohort_month
), cohort_counts AS (
  SELECT cohort_month,
    COUNT(*) AS total_customers,
    COUNTIF(has_full_90_day_followup) AS eligible_customers,
    COUNTIF(NOT has_full_90_day_followup) AS ineligible_customers,
    COUNTIF(has_full_90_day_followup
      AND days_to_first_later_day_purchase BETWEEN 1 AND 30) AS eligible_repeaters_30d,
    COUNTIF(has_full_90_day_followup
      AND days_to_first_later_day_purchase BETWEEN 1 AND 60) AS eligible_repeaters_60d,
    COUNTIF(has_full_90_day_followup
      AND repeat_purchase_within_90_days) AS eligible_repeaters_90d,
    COUNTIF(has_full_90_day_followup
      AND NOT repeat_purchase_within_90_days) AS eligible_nonrepeaters_90d
  FROM customer_histories
  GROUP BY cohort_month
), conditional_timing AS (
  -- Exact continuous median: only eligible customers repeating within days 1-90.
  -- DISTINCT yields one timing row per cohort before the final joins.
  SELECT DISTINCT cohort_month,
    PERCENTILE_CONT(CAST(days_to_first_later_day_purchase AS NUMERIC), NUMERIC '0.5')
      OVER (PARTITION BY cohort_month)
      AS median_days_to_first_later_day_purchase_among_eligible_90d_repeaters
  FROM customer_histories
  WHERE has_full_90_day_followup AND repeat_purchase_within_90_days
)
SELECT calendar.cohort_month,
  COALESCE(counts.total_customers, 0) AS total_customers,
  COALESCE(counts.eligible_customers, 0) AS eligible_customers,
  COALESCE(counts.ineligible_customers, 0) AS ineligible_customers,
  COALESCE(counts.eligible_repeaters_30d, 0) AS eligible_repeaters_30d,
  COALESCE(counts.eligible_repeaters_60d, 0) AS eligible_repeaters_60d,
  COALESCE(counts.eligible_repeaters_90d, 0) AS eligible_repeaters_90d,
  COALESCE(counts.eligible_nonrepeaters_90d, 0) AS eligible_nonrepeaters_90d,
  CAST(ROUND(SAFE_DIVIDE(
    CAST(COALESCE(counts.eligible_repeaters_30d, 0) AS BIGNUMERIC),
    CAST(COALESCE(counts.eligible_customers, 0) AS BIGNUMERIC)), 9) AS NUMERIC)
    AS repeat_rate_30d,
  CAST(ROUND(SAFE_DIVIDE(
    CAST(COALESCE(counts.eligible_repeaters_60d, 0) AS BIGNUMERIC),
    CAST(COALESCE(counts.eligible_customers, 0) AS BIGNUMERIC)), 9) AS NUMERIC)
    AS repeat_rate_60d,
  CAST(ROUND(SAFE_DIVIDE(
    CAST(COALESCE(counts.eligible_repeaters_90d, 0) AS BIGNUMERIC),
    CAST(COALESCE(counts.eligible_customers, 0) AS BIGNUMERIC)), 9) AS NUMERIC)
    AS repeat_rate_90d,
  timing.median_days_to_first_later_day_purchase_among_eligible_90d_repeaters,
  -- Display caution only: n < 30, including zero; not a significance test.
  COALESCE(counts.eligible_customers, 0) < 30 AS small_eligible_denominator
FROM cohort_calendar AS calendar
LEFT JOIN cohort_counts AS counts USING (cohort_month)
LEFT JOIN conditional_timing AS timing USING (cohort_month)
ORDER BY calendar.cohort_month;
