-- Run before transformations. Every failing_records value must be zero.
-- Expected constants are tied to the source SHA-256 in source_manifest.json.
SELECT 'raw_row_count' AS check_name,
  ABS(COUNT(*) - 1067371) AS failing_records
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.raw_lines`
UNION ALL
SELECT 'raw_signed_amount', IF(
  SUM(CAST(quantity AS NUMERIC) * unit_price) = NUMERIC '19287250.568', 0, 1)
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.raw_lines`
UNION ALL
SELECT 'raw_missing_customer_ids', ABS(COUNTIF(customer_id IS NULL) - 243007)
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.raw_lines`
UNION ALL
SELECT 'duplicate_source_keys', COUNT(*)
FROM (
  SELECT source_sheet, source_row
  FROM `{{PROJECT_ID}}.{{DATASET_ID}}.raw_lines`
  GROUP BY source_sheet, source_row HAVING COUNT(*) > 1
)
UNION ALL
SELECT 'unexpected_sheet_or_source_row', COUNTIF(
  source_sheet NOT IN ('Year 2009-2010', 'Year 2010-2011') OR source_row < 2)
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.raw_lines`
UNION ALL
SELECT 'rule_key_duplicates', COUNT(*)
FROM (
  SELECT stock_code FROM `{{PROJECT_ID}}.{{DATASET_ID}}.code_rules`
  GROUP BY stock_code HAVING COUNT(*) > 1
)
UNION ALL
SELECT 'invalid_rule_keys', COUNTIF(stock_code != UPPER(TRIM(stock_code)))
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.code_rules`
UNION ALL
SELECT 'missing_or_invalid_source_types', COUNTIF(
  description_source_type NOT IN ('text', 'numeric', 'missing')
  OR (description_source_type = 'missing' AND description_raw IS NOT NULL)
  OR (description_source_type != 'missing' AND description_raw IS NULL))
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.raw_lines`;

-- Compare the full overlapping multiset; equal totals alone cannot prove it matches.
SELECT 'overlap_pattern_multiplicity_mismatch' AS check_name,
  COUNT(*) AS failing_records
FROM (
  SELECT invoice_no, stock_code_raw, description_raw, quantity, invoice_datetime,
    unit_price, customer_id, country, description_source_type,
    COUNTIF(source_sheet = 'Year 2009-2010') AS earlier_count,
    COUNTIF(source_sheet = 'Year 2010-2011') AS later_count
  FROM `{{PROJECT_ID}}.{{DATASET_ID}}.raw_lines`
  WHERE invoice_datetime >= DATETIME '2010-12-01 00:00:00'
    AND invoice_datetime <= DATETIME '2010-12-09 20:01:00'
  GROUP BY invoice_no, stock_code_raw, description_raw, quantity, invoice_datetime,
    unit_price, customer_id, country, description_source_type
  HAVING earlier_count != later_count
);

-- Compare to per-sheet controls, also shown in load_manifest.json.
SELECT source_sheet, COUNT(*) AS `rows`,
  COUNT(DISTINCT invoice_no) AS invoices,
  COUNT(DISTINCT customer_id) AS identified_customers,
  COUNTIF(customer_id IS NULL) AS missing_customer_id_rows,
  SUM(CAST(quantity AS NUMERIC) * unit_price) AS signed_amount_gbp,
  SUM(GREATEST(CAST(quantity AS NUMERIC) * unit_price, NUMERIC '0')) AS positive_amount_gbp,
  SUM(LEAST(CAST(quantity AS NUMERIC) * unit_price, NUMERIC '0')) AS negative_amount_gbp
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.raw_lines`
GROUP BY source_sheet ORDER BY source_sheet;
