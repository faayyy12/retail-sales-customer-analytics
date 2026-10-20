-- One row per qualifying purchase invoice. Pending BigQuery execution.
-- Run validation; invoices spanning multiple qualifying calendar dates need review.
-- Source-line dates remain available; invoice date is earliest qualifying date.
-- Keep historical data unpartitioned under sandbox partition expiration.
-- Plain CREATE protects existing tables; normal table expiration is inherited.
CREATE TABLE `{{PROJECT_ID}}.{{DATASET_ID}}.purchase_invoices`
CLUSTER BY customer_id, invoice_no
AS
SELECT
  invoice_no,
  MIN(invoice_datetime) AS invoice_datetime,
  MIN(invoice_date) AS invoice_date,
  MAX(invoice_datetime) AS last_qualifying_line_datetime,
  COUNT(DISTINCT invoice_date) AS qualifying_calendar_days,
  COUNT(DISTINCT customer_id) AS identified_customer_count,
  MAX(customer_id) AS customer_id,
  COUNT(DISTINCT country) AS country_count,
  MAX(country) AS country,
  COUNT(*) AS qualifying_line_count,
  SUM(gross_merchandise_amount) AS gross_merchandise_amount,
  MIN(invoice_datetime) < DATETIME '2011-12-01 00:00:00' AS is_in_main_history
FROM `{{PROJECT_ID}}.{{DATASET_ID}}.clean_lines`
WHERE line_status = 'qualifying_purchase'
GROUP BY invoice_no;
