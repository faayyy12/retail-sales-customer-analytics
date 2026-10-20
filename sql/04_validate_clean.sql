-- Pending BigQuery execution. Every failing_records value must be zero.
SELECT 'canonical_row_count' AS check_name,
  ABS(COUNT(*) - 1044848) AS failing_records
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.canonical_lines`
UNION ALL
SELECT 'canonical_signed_amount', IF(SUM(line_amount) = NUMERIC '18909762.118', 0, 1)
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.canonical_lines`
UNION ALL
SELECT 'overlap_exclusion_count', ABS(COUNT(*) - 22523)
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.overlap_exclusions`
UNION ALL
SELECT 'overlap_exclusion_amount', IF(SUM(line_amount) = NUMERIC '377488.45', 0, 1)
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.overlap_exclusions`
UNION ALL
SELECT 'clean_join_row_count', ABS(
  (SELECT COUNT(*) FROM `{{PROJECT_ID}}.{{DATASET_ID}}.clean_lines`) -
  (SELECT COUNT(*) FROM `{{PROJECT_ID}}.{{DATASET_ID}}.canonical_lines`))
UNION ALL
SELECT 'clean_signed_amount_preserved', IF(SUM(line_amount) = NUMERIC '18909762.118', 0, 1)
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.clean_lines`
UNION ALL
SELECT 'clean_duplicate_source_keys', COUNT(*) FROM (
  SELECT source_sheet, source_row
  FROM `{{PROJECT_ID}}.{{DATASET_ID}}.clean_lines`
  GROUP BY source_sheet, source_row HAVING COUNT(*) > 1
)
UNION ALL
SELECT 'invalid_purchase_rows', COUNTIF(line_status = 'qualifying_purchase' AND (
  quantity <= 0 OR unit_price <= 0 OR is_cancellation OR item_category != 'merchandise'
  OR NOT description_is_valid))
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.clean_lines`
UNION ALL
SELECT 'invalid_credit_rows', COUNTIF(line_status = 'recorded_merchandise_credit' AND (
  quantity >= 0 OR unit_price <= 0 OR NOT is_cancellation OR item_category != 'merchandise'
  OR NOT description_is_valid))
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.clean_lines`
UNION ALL
SELECT 'invoice_key_duplicates', COUNT(*) FROM (
  SELECT invoice_no FROM `{{PROJECT_ID}}.{{DATASET_ID}}.purchase_invoices`
  GROUP BY invoice_no HAVING COUNT(*) > 1
)
UNION ALL
SELECT 'invoice_conflicting_days_customers_countries', COUNTIF(
  qualifying_calendar_days != 1 OR identified_customer_count > 1 OR country_count != 1)
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.purchase_invoices`
UNION ALL
SELECT 'invoice_line_money_reconciliation', IF(
  (SELECT SUM(gross_merchandise_amount) FROM `{{PROJECT_ID}}.{{DATASET_ID}}.purchase_invoices`) =
  (SELECT SUM(gross_merchandise_amount) FROM `{{PROJECT_ID}}.{{DATASET_ID}}.clean_lines`), 0, 1);

-- Category/period controls: compare with reports/cleaning_category_controls.csv.
-- Period labels partition the source; no reporting cutoff is applied silently.
SELECT
  CASE
    WHEN invoice_datetime < DATETIME '2010-01-01 00:00:00' THEN '2009_history'
    WHEN invoice_datetime < DATETIME '2010-12-01 00:00:00' THEN '2010_jan_nov'
    WHEN invoice_datetime < DATETIME '2011-01-01 00:00:00' THEN '2010_dec_history'
    WHEN invoice_datetime < DATETIME '2011-12-01 00:00:00' THEN '2011_jan_nov'
    ELSE '2011_dec_partial'
  END AS period,
  item_category, line_status, COUNT(*) AS `rows`,
  COUNT(DISTINCT invoice_no) AS invoices,
  COUNT(DISTINCT customer_id) AS identified_customers,
  COUNTIF(customer_id IS NULL) AS missing_customer_id_rows,
  SUM(line_amount) AS signed_amount_gbp,
  SUM(gross_merchandise_amount) AS gross_merchandise_amount,
  SUM(recorded_merchandise_credit_amount) AS recorded_merchandise_credit_amount,
  SUM(identified_gross_merchandise_amount) AS identified_gross_merchandise_amount
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.clean_lines`
GROUP BY period, item_category, line_status
ORDER BY period, item_category, line_status;
