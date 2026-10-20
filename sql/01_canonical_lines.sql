-- GoogleSQL template. Render placeholders before use. Pending BigQuery execution.
-- SELECT-based table creation; no INSERT/UPDATE/DELETE/MERGE.
-- Do not overwrite existing tables implicitly. Use a fresh dedicated dataset/version
-- for a rebuild. Repeated sandbox rebuilds also consume lifetime storage allowance.
-- Sandbox: keep this historical table unpartitioned so old source-date partitions
-- cannot expire immediately under the sandbox's 60-day partition expiration.
CREATE TABLE `{{PROJECT_ID}}.{{DATASET_ID}}.canonical_lines`
CLUSTER BY invoice_no, customer_id, stock_code_raw
AS
SELECT
  invoice_no, stock_code_raw, description_raw, quantity, invoice_datetime,
  unit_price, customer_id, country, source_sheet, source_row,
  description_source_type,
  CAST(quantity AS NUMERIC) * unit_price AS line_amount
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.raw_lines`
WHERE (source_sheet = 'Year 2009-2010'
       AND invoice_datetime < DATETIME '2010-12-01 00:00:00')
   OR source_sheet = 'Year 2010-2011';

-- Retain all excluded row references and original values in a separate audit layer.
CREATE TABLE `{{PROJECT_ID}}.{{DATASET_ID}}.overlap_exclusions` AS
SELECT
  invoice_no, stock_code_raw, description_raw, quantity, invoice_datetime,
  unit_price, customer_id, country, source_sheet, source_row,
  description_source_type,
  CAST(quantity AS NUMERIC) * unit_price AS line_amount,
  'overlapping_earlier_extract' AS exclusion_reason
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.raw_lines`
WHERE source_sheet = 'Year 2009-2010'
  AND invoice_datetime >= DATETIME '2010-12-01 00:00:00';
