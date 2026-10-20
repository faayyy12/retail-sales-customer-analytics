-- One read-only statement; manual execution and export reconciliation pending.
-- Full-period distinct customers: NEVER sum monthly customer counts.
-- purchase_invoices contains only qualifying purchases, one row per invoice.
WITH periods AS (
  SELECT '2010_jan_nov' AS period, DATE '2010-01-01' AS start_date,
    DATE '2010-12-01' AS end_date
  UNION ALL
  SELECT '2011_jan_nov', DATE '2011-01-01', DATE '2011-12-01'
), period_totals AS (
  -- The two non-overlapping windows assign each invoice to at most one period.
  -- Anonymous invoices remain separate and never contribute a customer ID.
  SELECT
    p.period,
    COUNT(DISTINCT i.customer_id) AS identified_purchasing_customers,
    COUNTIF(i.customer_id IS NOT NULL) AS identified_purchase_invoice_count,
    COALESCE(SUM(IF(i.customer_id IS NOT NULL,
      i.gross_merchandise_amount, NUMERIC '0')), NUMERIC '0')
      AS identified_gross_merchandise_sales_gbp,
    COUNTIF(i.invoice_no IS NOT NULL AND i.customer_id IS NULL)
      AS anonymous_purchase_invoice_count,
    COALESCE(SUM(IF(i.customer_id IS NULL,
      i.gross_merchandise_amount, NUMERIC '0')), NUMERIC '0')
      AS anonymous_gross_merchandise_sales_gbp,
    COUNT(i.invoice_no) AS total_purchase_invoice_count,
    COALESCE(SUM(i.gross_merchandise_amount), NUMERIC '0')
      AS total_gross_merchandise_sales_gbp
  FROM periods AS p
  LEFT JOIN `{{PROJECT_ID}}.{{DATASET_ID}}.purchase_invoices` AS i
    ON i.invoice_date >= p.start_date AND i.invoice_date < p.end_date
  GROUP BY p.period
)
SELECT
  period,
  identified_purchasing_customers,
  identified_purchase_invoice_count,
  CAST(ROUND(SAFE_DIVIDE(CAST(identified_purchase_invoice_count AS BIGNUMERIC),
    CAST(identified_purchasing_customers AS BIGNUMERIC)),
    9, 'ROUND_HALF_AWAY_FROM_ZERO') AS NUMERIC) AS purchases_per_identified_customer,
  CAST(ROUND(SAFE_DIVIDE(CAST(identified_gross_merchandise_sales_gbp AS BIGNUMERIC),
    CAST(identified_purchase_invoice_count AS BIGNUMERIC)),
    9, 'ROUND_HALF_AWAY_FROM_ZERO') AS NUMERIC) AS average_identified_purchase_invoice_value_gbp,
  identified_gross_merchandise_sales_gbp,
  anonymous_purchase_invoice_count,
  anonymous_gross_merchandise_sales_gbp,
  total_purchase_invoice_count,
  total_gross_merchandise_sales_gbp,
  CAST(ROUND(SAFE_DIVIDE(CAST(identified_gross_merchandise_sales_gbp AS BIGNUMERIC),
    CAST(total_gross_merchandise_sales_gbp AS BIGNUMERIC)),
    9, 'ROUND_HALF_AWAY_FROM_ZERO') AS NUMERIC) AS identified_gross_sales_share
-- The exact identity uses C * (I/C) * (G/I) = G, with C, I, G above.
-- The comparison reconstructs these fractions from the exact counts and money.
-- Do NOT require a zero residual after multiplying the 9-decimal export ratios.
-- This identity follows from the definitions; population/baseline checks also matter.
FROM period_totals
ORDER BY period;
