-- One read-only GoogleSQL statement; manual execution/export comparison pending.
-- Feature population: identified qualifying purchases through 2011-08-31.
-- Outcome: 2011-09-01 through 2011-11-29 inclusive, exactly 90 calendar days.
-- Historical features and future outcomes aggregate independently before joining.
-- No segments/thresholds here. First observed is not acquisition; gross is not profit.
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
)
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
ORDER BY features.customer_id;
