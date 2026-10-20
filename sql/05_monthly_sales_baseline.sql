-- One read-only statement; manual BigQuery execution/reconciliation pending.
-- Compare [2010-01-01, 2010-12-01) with [2011-01-01, 2011-12-01).
-- No December in either year; source dates have no assumed timezone.
-- Prerequisite: verified invoice key/customer/calendar-day consistency.
WITH months AS (
  SELECT month_start
  FROM UNNEST(GENERATE_DATE_ARRAY(
    DATE '2010-01-01', DATE '2011-11-01', INTERVAL 1 MONTH)) AS month_start
  WHERE EXTRACT(MONTH FROM month_start) <= 11
), monthly_purchases AS (
  -- Already one row per qualifying invoice; includes anonymous purchases.
  SELECT
    DATE_TRUNC(invoice_date, MONTH) AS month_start,
    SUM(gross_merchandise_amount) AS gross_merchandise_sales_gbp,
    COUNT(*) AS purchase_invoice_count,
    COUNT(DISTINCT customer_id) AS identified_purchasing_customers,
    COUNTIF(customer_id IS NOT NULL) AS identified_purchase_invoice_count,
    COUNTIF(customer_id IS NULL) AS anonymous_purchase_invoice_count,
    SUM(IF(customer_id IS NOT NULL, gross_merchandise_amount, NUMERIC '0'))
      AS identified_gross_merchandise_sales_gbp
  FROM `{{PROJECT_ID}}.{{DATASET_ID}}.purchase_invoices`
  WHERE (invoice_date >= DATE '2010-01-01' AND invoice_date < DATE '2010-12-01')
     OR (invoice_date >= DATE '2011-01-01' AND invoice_date < DATE '2011-12-01')
  GROUP BY month_start
), monthly_credits AS (
  -- Positive magnitudes on CREDIT recorded dates, not original purchase dates.
  SELECT
    DATE_TRUNC(invoice_date, MONTH) AS month_start,
    SUM(recorded_merchandise_credit_amount) AS recorded_merchandise_credits_gbp
  FROM `{{PROJECT_ID}}.{{DATASET_ID}}.clean_lines`
  WHERE line_status = 'recorded_merchandise_credit'
    AND ((invoice_date >= DATE '2010-01-01' AND invoice_date < DATE '2010-12-01')
      OR (invoice_date >= DATE '2011-01-01' AND invoice_date < DATE '2011-12-01'))
  GROUP BY month_start
), baseline AS (
  -- Each input has at most one row per month before joining: no multiplication.
  SELECT
    m.month_start,
    COALESCE(p.gross_merchandise_sales_gbp, NUMERIC '0') AS gross_merchandise_sales_gbp,
    COALESCE(c.recorded_merchandise_credits_gbp, NUMERIC '0') AS recorded_merchandise_credits_gbp,
    COALESCE(p.purchase_invoice_count, 0) AS purchase_invoice_count,
    COALESCE(p.identified_purchasing_customers, 0) AS identified_purchasing_customers,
    COALESCE(p.identified_purchase_invoice_count, 0) AS identified_purchase_invoice_count,
    COALESCE(p.anonymous_purchase_invoice_count, 0) AS anonymous_purchase_invoice_count,
    COALESCE(p.identified_gross_merchandise_sales_gbp, NUMERIC '0') AS identified_gross_merchandise_sales_gbp
  FROM months AS m
  LEFT JOIN monthly_purchases AS p USING (month_start)
  LEFT JOIN monthly_credits AS c USING (month_start)
)
SELECT
  month_start,
  gross_merchandise_sales_gbp,
  recorded_merchandise_credits_gbp,
  -- Defined merchandise measure; not profit or audited accounting revenue.
  gross_merchandise_sales_gbp - recorded_merchandise_credits_gbp AS net_recorded_merchandise_sales_gbp,
  purchase_invoice_count,
  -- Only division outputs have a fixed precision: 9 decimals, half away from zero.
  -- Wider division avoids premature NUMERIC rounding before the explicit ROUND.
  CAST(ROUND(SAFE_DIVIDE(CAST(gross_merchandise_sales_gbp AS BIGNUMERIC),
    CAST(purchase_invoice_count AS BIGNUMERIC)), 9, 'ROUND_HALF_AWAY_FROM_ZERO')
    AS NUMERIC) AS average_purchase_invoice_value_gbp,
  identified_purchasing_customers,
  identified_purchase_invoice_count,
  anonymous_purchase_invoice_count,
  identified_gross_merchandise_sales_gbp,
  CAST(ROUND(SAFE_DIVIDE(CAST(identified_gross_merchandise_sales_gbp AS BIGNUMERIC),
    CAST(gross_merchandise_sales_gbp AS BIGNUMERIC)), 9, 'ROUND_HALF_AWAY_FROM_ZERO')
    AS NUMERIC) AS identified_gross_sales_share
FROM baseline
ORDER BY month_start;
