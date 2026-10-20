-- BigQuery execution and dry-run verification are pending.
-- code_rules must have unique stock_code keys; run 00 checks before this join.
-- Keep historical records unpartitioned under sandbox partition expiration.
-- Plain CREATE fails on an existing destination; table expiration is inherited.
CREATE TABLE `{{PROJECT_ID}}.{{DATASET_ID}}.clean_lines`
CLUSTER BY invoice_no, customer_id, stock_code
AS
WITH code_lookup AS (
  -- One row per lookup key prevents join multiplication.
  -- Do not choose an arbitrary category if a matched key has duplicate rules.
  SELECT stock_code,
    IF(COUNT(*) = 1, MIN(item_category),
      ERROR('Duplicate stock_code in code_rules: stop and repair the lookup.'))
      AS item_category
  FROM `{{PROJECT_ID}}.{{DATASET_ID}}.code_rules`
  GROUP BY stock_code
), normalized AS (
  SELECT
    invoice_no, stock_code_raw, description_raw, quantity, invoice_datetime,
    DATE(invoice_datetime) AS invoice_date, unit_price, customer_id, country,
    source_sheet, source_row, description_source_type, line_amount,
    UPPER(TRIM(stock_code_raw)) AS stock_code,
    TRIM(description_raw) AS description,
    STARTS_WITH(UPPER(invoice_no), 'C') AS is_cancellation,
    customer_id IS NOT NULL AS has_customer_id,
    invoice_datetime < DATETIME '2011-12-01 00:00:00' AS is_in_main_history
  FROM `{{PROJECT_ID}}.{{DATASET_ID}}.canonical_lines`
), categorized AS (
  SELECT
    n.*,
    COALESCE(r.item_category,
      IF(REGEXP_CONTAINS(n.stock_code, r'^\d{5}[A-Z]*$'),
         'merchandise', 'unresolved_special')) AS item_category,
    COALESCE(description_source_type = 'text'
      AND description != ''
      AND NOT REGEXP_CONTAINS(description,
        r'(?i)^(?:\?+|CHECK(?:ED)?|DAMAGED?|FAULTY|LOST|MISSING|FOUND|ADJUST(?:MENT)?|EBAY|AMAZON|UPDATE|INCORRECT|WRONG|GIVEN|SOLD|HISTORIC)(?:\b|$)'),
      FALSE) AS description_is_valid
  FROM normalized AS n
  LEFT JOIN code_lookup AS r
    ON n.stock_code = r.stock_code
), classified AS (
  SELECT *,
    CASE
      WHEN unit_price < 0 THEN 'negative_price_adjustment'
      WHEN unit_price = 0 OR quantity = 0 THEN 'zero_value'
      WHEN item_category != 'merchandise' THEN 'non_merchandise_or_unresolved'
      WHEN NOT description_is_valid THEN 'unresolved_description'
      WHEN quantity > 0 AND REGEXP_CONTAINS(invoice_no, r'^\d{6}$')
        THEN 'qualifying_purchase'
      WHEN quantity < 0 AND is_cancellation
        THEN 'recorded_merchandise_credit'
      ELSE 'unresolved_sign_or_invoice'
    END AS line_status
  FROM categorized
)
SELECT *,
  IF(line_status = 'qualifying_purchase', line_amount, NUMERIC '0')
    AS gross_merchandise_amount,
  IF(line_status = 'recorded_merchandise_credit', -line_amount, NUMERIC '0')
    AS recorded_merchandise_credit_amount,
  IF(line_status = 'qualifying_purchase' AND has_customer_id,
    line_amount, NUMERIC '0') AS identified_gross_merchandise_amount
FROM classified;
