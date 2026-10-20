# Analytical methodology

## Source and provenance

UCI Online Retail II contains 1,067,371 transaction lines across two Excel sheets.
The original workbook is preserved in `data/raw/`; provenance and license are in
[the source manifest](../reports/source_manifest.json). Raw identifiers remain
strings, missing customer IDs remain missing, and each line carries its source
sheet and original Excel row number. Source timestamps have no specified timezone.

The [source profile](../reports/source_profile.json) records complete-sheet
counts, date ranges, missingness, repeated patterns and independent XML controls.
Monetary arithmetic uses Decimal representations of source numeric prices and
BigQuery NUMERIC; prices can have three decimal places. Rounding is for display
or explicitly defined nine-place ratios, not intermediate monetary sums.

## Overlap and repeated lines

The two sheets contain identical overlapping extracts for 1-9 December 2010:
22,523 copied rows, with equal multiplicities across 22,202 distinct patterns.
Use the first sheet before 1 December 2010 and the second sheet thereafter.
This leaves 1,044,848 canonical lines; excluded source references remain in
`data/processed/overlap_exclusions.csv`. Other repeated lines are retained because
repeated invoice/product entries may be legitimate. Invoice plus product is not
a unique line key.

## Classification and populations

[Code rules](code_rules.csv) distinguish merchandise, postage, fees, discounts,
manual entries and unresolved adjustments. The rule lookup must be unique by
normalized stock code; ambiguous matched rules fail instead of multiplying rows.
A qualifying purchase is a non-cancellation invoice with eligible merchandise
lines of positive quantity and price. Missing or non-text descriptions do not
silently become merchandise. Large wholesale purchases are retained.

Classified negative merchandise entries form recorded credits on their recorded
dates. They do not prove physical returns and are not silently matched to prior
invoices. Unresolved adjustments remain separate. Anonymous qualifying purchases
stay in sales totals but never form an invented customer in customer analyses.
See [numerical cleaning controls](../reports/analysis/cleaning_controls.json).

## Periods, grains and joins

| Result | Grain / key | Boundary or join rule |
|---|---|---|
| Raw/canonical/clean lines | `(source_sheet, source_row)` | One-to-one classifications; unique code lookup |
| Purchase invoices | `invoice_no` | Validate customer, country and calendar-day consistency before grouping |
| Monthly sales | `month_start` | January-November 2010 and 2011; aggregate invoices and credits separately before joining to a calendar |
| Sales periods | `period` / `year` | Recompute ratios from period totals; count distinct customers over the whole period |
| First-observed histories | `customer_id` | Full qualifying history through 30 November 2011 |
| Cohorts | `cohort_month` | One aggregate per first-observed month, including unobservable cohorts |
| Snapshot | `customer_id` | Features through 31 August 2011; later-only IDs cannot enter |
| Segments | `(scenario_id, segment_id)` | Five groups in each of seven settings; never sum across settings |

Do not join line detail to aggregated invoice/month totals and then sum those
totals. Monthly distinct customers are not additive. For identical identified
populations, gross equals active customers times invoices per customer times
average invoice value. Compare this identity using exact operands, not rounded
dashboard labels. The two annual populations are not a matched-customer panel.

## Repeat purchasing

Use the first observed qualifying date, not an acquisition date. A repeat must
occur on a strictly later calendar day; same-day invoices alone do not count.
Eligibility requires first date plus 90 days to be on or before 30 November 2011.
All 30/60/90-day rates use this same fully eligible denominator. Every incomplete
90-day outcome is NULL, even if an early repeat is already visible.

October-November 2011 have zero eligible customers: their rates and timing remain
blank. September has only three eligible customers and is flagged as small.
Conditional median timing describes eligible customers who repeat within 90
days; do not average cohort medians to obtain the pooled median.

## Historical customer groups

Features and thresholds use only history through 31 August 2011. Outcomes cover
1 September-29 November inclusive, exactly 90 calendar days, with FALSE/zero for
no subsequent purchase. The feature and outcome aggregates join on unique
historical customer keys; future outcomes cannot alter features or membership.

[Fixed rules](customer_segment_rules.json) assign, in order: recently first
observed; recent established higher-spend repeat; other recent established;
less-recent multi-day; less-recent single-day. The primary recency/tenure
thresholds are 90 days, repeat history is at least three distinct purchase days,
and higher spending is at least GBP 2,000. Six one-at-a-time alternatives vary
recency, observed tenure or spending thresholds. The base specification remains
primary; alternatives are sensitivity checks, not a search for an outcome winner.

Outcomes had been inspected before this segmentation exercise, so this is not
an untouched holdout. Associations do not establish churn, outreach response,
incremental revenue or intervention effects. Source boundaries, anonymous sales,
unequal observed histories and large wholesale invoices limit generalization.
