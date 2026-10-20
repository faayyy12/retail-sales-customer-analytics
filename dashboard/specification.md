# Retail dashboard specification

[Open Tableau Public](https://public.tableau.com/app/profile/fab.li/viz/RetailSalesCustomerAnalytics/CustomerPriorities).
The audience is a commercial manager investigating recorded sales and customer
activity. Use GBP, visible date windows and plain denominator definitions.

| Page | Main views | Preview |
|---|---|---|
| Sales Overview | Annual gross, invoices, average invoice, ID coverage, credits and gross less credits; monthly trends and identified/anonymous split | [Screenshot](<screenshots/Sales Overview.png>) |
| Repeat Purchasing | Total/eligible/ineligible customers, 30/60/90-day rates, cohort eligibility, blank-aware rate table and conditional first-repeat timing | [Screenshot](<screenshots/Repeat Purchasing.png>) |
| Customer Priorities | Snapshot customers, subsequent purchasers/gross/rate; five groups by size, rate, customer/gross share and gross per customer | [Screenshot](<screenshots/Customer Priorities.png>) |

## Independent data sources

| Input | Grain | Rows |
|---|---|---:|
| [Sales periods](../exports/tableau_sales_periods.csv) | January-November comparison period / year | 2 |
| [Sales months](../reports/bigquery_monthly_sales_baseline.csv) | Calendar month in the two comparison periods | 22 |
| [First-observed cohorts](../reports/bigquery_cohort_purchase_timing.csv) | First-observed month | 24 |
| [Base customer groups](../exports/tableau_customer_segments_base.csv) | Fixed primary group | 5 |

Keep these sources separate: do not join or union different grains. The optional
[sensitivity input](../exports/tableau_customer_segments_sensitivity.csv) contains
30 rows across six alternative settings, not 30 independent customer populations.
It must never change base-page totals or be summed across settings.

## Calculation and interaction rules

- Sales compares January-November 2010 and 2011. Annual average invoice and
  coverage divide period totals; never average monthly ratios or sum monthly
  distinct customer counts. Credits use their recorded months.
- Repeat uses all history through 30 November 2011, later-day purchases and one
  fully 90-day-eligible denominator. A cohort selector changes the selected
  customers, not the follow-up cutoff. October-November rates/timing stay blank;
  September's three eligible customers are flagged as small.
- Timing is conditional on eligible repetition within 90 days. Cohort medians
  cannot be averaged into a pooled median.
- Customer Priorities uses features through 31 August and outcomes through
  29 November inclusive. Include non-purchasers in group denominators.
- Both share calculations address Segment Id over all five base groups. Use
  highlighting rather than filtering out denominator groups. Month, segment
  and cohort highlighting should preserve aggregate populations.

Key controls are 5,824 first-observed customers, 5,227 eligible and 597 ineligible;
18.63% / 34.26% / 44.10% pooled repeat rates; 5,224 snapshot customers and
GBP 2,699,494.19 later gross. Higher-spend repeat customers have 17.53% customer
share and 67.23% subsequent gross share. Recheck displayed values and reset
controls against CSV inputs after a workbook change.

The screenshots are static previews and do not establish current public rendering
or interactive behavior. Tableau workbooks and extracts are not distributed
with this repository. See [metrics](../docs/metrics.md),
[methodology](../docs/methodology.md) and [reproduction](../docs/reproduction.md).
