-- One read-only GoogleSQL statement; manual execution/reconciliation pending.
-- Full earlier qualifying history, including December 2009 and December 2010.
-- First observed is not acquisition. Source calendar dates retain no assumed timezone.
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
)
SELECT customer_id, first_observed_purchase_date, cohort_month,
  first_later_day_purchase_date, days_to_first_later_day_purchase,
  has_full_90_day_followup,
  CASE
    -- Unknown for every ineligible customer, even if a later-day purchase is visible.
    WHEN NOT has_full_90_day_followup THEN CAST(NULL AS BOOL)
    ELSE COALESCE(days_to_first_later_day_purchase BETWEEN 1 AND 90, FALSE)
  END AS repeat_purchase_within_90_days
FROM histories
ORDER BY customer_id;
