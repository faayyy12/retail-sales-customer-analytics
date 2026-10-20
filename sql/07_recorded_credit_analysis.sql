-- One read-only statement. Manual execution/export reconciliation pending.
-- Recorded credit dates only; no purchase matching or large-record exclusions.
-- Month contributions reuse the already-reconciled monthly baseline CSV.
WITH periods AS (
  SELECT '2010_jan_nov' AS period
  UNION ALL SELECT '2011_jan_nov'
), credit_lines AS (
  SELECT
    IF(invoice_date < DATE '2011-01-01', '2010_jan_nov', '2011_jan_nov') AS period,
    invoice_no, stock_code, recorded_merchandise_credit_amount AS credit_amount
  FROM `{{PROJECT_ID}}.{{DATASET_ID}}.clean_lines`
  WHERE line_status = 'recorded_merchandise_credit'
    AND ((invoice_date >= DATE '2010-01-01' AND invoice_date < DATE '2010-12-01')
      OR (invoice_date >= DATE '2011-01-01' AND invoice_date < DATE '2011-12-01'))
), period_aggregates AS (
  SELECT period, COUNT(DISTINCT invoice_no) AS credit_invoice_count,
    COUNT(*) AS credit_line_count, SUM(credit_amount) AS recorded_credits_gbp
  FROM credit_lines GROUP BY period
), period_totals AS (
  SELECT p.period, COALESCE(a.credit_invoice_count, 0) AS credit_invoice_count,
    COALESCE(a.credit_line_count, 0) AS credit_line_count,
    COALESCE(a.recorded_credits_gbp, NUMERIC '0') AS recorded_credits_gbp
  FROM periods AS p LEFT JOIN period_aggregates AS a USING (period)
), invoice_totals AS (
  -- Credit invoice grain is (period, invoice_no); retain all classified lines.
  SELECT period, invoice_no, COUNT(*) AS credit_line_count,
    SUM(credit_amount) AS recorded_credits_gbp
  FROM credit_lines GROUP BY period, invoice_no
), invoice_ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY period ORDER BY recorded_credits_gbp DESC, invoice_no ASC) AS credit_rank
  FROM invoice_totals
), invoice_display AS (
  SELECT period, invoice_no AS detail_key, credit_rank AS row_rank,
    1 AS credit_invoice_count, credit_line_count, recorded_credits_gbp
  FROM invoice_ranked WHERE credit_rank <= 10
  UNION ALL
  -- Always include a remainder, including zero if no invoices remain.
  SELECT p.period, '__REMAINDER__', 11,
    COUNTIF(i.credit_rank > 10),
    COALESCE(SUM(IF(i.credit_rank > 10, i.credit_line_count, 0)), 0),
    COALESCE(SUM(IF(i.credit_rank > 10, i.recorded_credits_gbp, NUMERIC '0')), NUMERIC '0')
  FROM periods AS p LEFT JOIN invoice_ranked AS i USING (period)
  GROUP BY p.period
), stock_totals AS (
  -- Aggregate lines first; do not join invoice totals back to detail lines.
  SELECT stock_code,
    COUNTIF(period = '2010_jan_nov') AS lines_2010,
    COUNTIF(period = '2011_jan_nov') AS lines_2011,
    SUM(IF(period = '2010_jan_nov', credit_amount, NUMERIC '0')) AS credits_2010,
    SUM(IF(period = '2011_jan_nov', credit_amount, NUMERIC '0')) AS credits_2011
  FROM credit_lines GROUP BY stock_code
), stock_changes AS (
  SELECT *, credits_2011 - credits_2010 AS change_gbp FROM stock_totals
), stock_increases AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY change_gbp DESC, stock_code ASC) AS credit_rank
  FROM stock_changes WHERE change_gbp > NUMERIC '0'
), stock_decreases AS (
  SELECT *, ROW_NUMBER() OVER (ORDER BY change_gbp ASC, stock_code ASC) AS credit_rank
  FROM stock_changes WHERE change_gbp < NUMERIC '0'
), stock_selected AS (
  SELECT stock_code AS detail_key, credit_rank AS row_rank,
    lines_2010, lines_2011, credits_2010, credits_2011
  FROM stock_increases WHERE credit_rank <= 10
  UNION ALL
  SELECT stock_code, credit_rank + 10, lines_2010, lines_2011, credits_2010, credits_2011
  FROM stock_decreases WHERE credit_rank <= 10
), stock_display AS (
  SELECT * FROM stock_selected
  UNION ALL
  -- Selected positive/negative sets are disjoint; all other codes form one remainder.
  SELECT '__REMAINDER__', 21,
    COALESCE(SUM(s.lines_2010), 0), COALESCE(SUM(s.lines_2011), 0),
    COALESCE(SUM(s.credits_2010), NUMERIC '0'), COALESCE(SUM(s.credits_2011), NUMERIC '0')
  FROM stock_changes AS s
  LEFT JOIN stock_selected AS chosen ON s.stock_code = chosen.detail_key
  WHERE chosen.detail_key IS NULL
), output_rows AS (
  SELECT 'period_summary' AS section, period, '__ALL__' AS detail_key, 0 AS row_rank,
    credit_invoice_count, credit_line_count, recorded_credits_gbp
  FROM period_totals
  UNION ALL
  SELECT 'invoice_concentration', period, detail_key, row_rank,
    credit_invoice_count, credit_line_count, recorded_credits_gbp
  FROM invoice_display
  UNION ALL
  SELECT 'stock_change', p.period, s.detail_key, s.row_rank,
    -- An invoice can contain several codes: distinct counts are not additive by code.
    CAST(NULL AS INT64),
    IF(p.period = '2010_jan_nov', s.lines_2010, s.lines_2011),
    IF(p.period = '2010_jan_nov', s.credits_2010, s.credits_2011)
  FROM stock_display AS s CROSS JOIN periods AS p
)
SELECT
  o.section, o.period, o.detail_key, o.row_rank,
  o.credit_invoice_count, o.credit_line_count, o.recorded_credits_gbp,
  CAST(ROUND(SAFE_DIVIDE(CAST(o.recorded_credits_gbp AS BIGNUMERIC),
    CAST(o.credit_invoice_count AS BIGNUMERIC)), 9, 'ROUND_HALF_AWAY_FROM_ZERO')
    AS NUMERIC) AS average_credit_per_invoice_gbp,
  CAST(ROUND(SAFE_DIVIDE(CAST(o.recorded_credits_gbp AS BIGNUMERIC),
    CAST(t.recorded_credits_gbp AS BIGNUMERIC)), 9, 'ROUND_HALF_AWAY_FROM_ZERO')
    AS NUMERIC) AS share_of_period_credits
FROM output_rows AS o
JOIN period_totals AS t ON o.period = t.period
-- Sections are alternative views of the same population; do not sum across sections.
ORDER BY CASE o.section WHEN 'period_summary' THEN 1 WHEN 'invoice_concentration' THEN 2 ELSE 3 END,
  o.period, o.row_rank, o.detail_key;
