# Retail Revenue Drivers and Repeat Purchasing

[Explore the Tableau dashboard](https://public.tableau.com/app/profile/fab.li/viz/RetailSalesCustomerAnalytics/CustomerPriorities)

In this project, I analyzed around 1.07 million transaction records from a UK online retailer to understand how sales changed, how often customers bought again, and how different customer groups performed. The workflow uses Python for data cleaning and validation, BigQuery SQL for analysis, and Tableau for three interactive dashboards.

## Dashboard pages

### Sales Overview

How customer activity, invoice value and credits relate to sales changes.

<details>
<summary>View screenshot</summary>

![Sales Overview](<dashboard/screenshots/Sales Overview.png>)

</details>

### Repeat Purchasing

How often and how quickly customers buy again after their first recorded purchase,
using only customers with a full follow-up period.

<details>
<summary>View screenshot</summary>

![Repeat Purchasing](<dashboard/screenshots/Repeat Purchasing.png>)

</details>

### Customer Priorities

How five customer groups differ in later purchases and their share of sales.

<details>
<summary>View screenshot</summary>

![Customer Priorities](<dashboard/screenshots/Customer Priorities.png>)

</details>

## Main findings

1. **Gross sales rose 2.89%, with 3.71% fewer invoices and 6.85% higher average
   invoice value.** Sales linked to customer IDs were nearly flat; records without
   customer IDs account for the increase. Recorded credits rose GBP 65,618.68.
   Check how customer IDs were recorded, the credit records, and the mix of products
   and wholesale orders before linking growth to pricing or stronger demand.
2. **44.10% of customers with full follow-up bought again within 90 days of their
   first recorded purchase.** The 30/60-day rates are 18.63% / 34.26%. All three
   rates use the same 5,227 customers with a full 90 days of follow-up. The 597
   without enough follow-up are excluded, rather than counted as customers who
   did not return. Compare customers over equal periods and consider how often
   they usually buy before assuming that inactivity means they have left.
3. **17.53% of customers grouped as of 31 August 2011 account for 67.23% of later
   gross sales.** These recent, higher-spending repeat customers have an 82.97%
   later purchase rate. Review their service needs and investigate customers who
   have not bought recently as separate groups. These patterns do not prove who
   would respond to outreach or whether it would increase sales.

Read the [findings and practical recommendations](reports/analysis/findings.md),
with [supporting figures](reports/analysis/summary.json).

## Python → BigQuery → Tableau

- **Python/Pandas:** keep the original workbook and source details, check overlapping
  records, classify transactions, prepare CSVs with defined data types, and
  independently check the results.
- **BigQuery GoogleSQL:** create one consistent set of transaction records and
  invoices that meet the purchase rules, then analyze sales changes, repeat
  purchasing and customer groups.
- **Tableau Public:** show sales, groups based on first recorded purchase month,
  and customer groups on three dashboards. State which customers each rate includes,
  and use filters and highlighting to explore the related charts.

The repository includes source code, SQL templates, data schemas, fixed dependency
versions, tests, original data, prepared files and supporting results. Tableau
workbooks and extracts are not included; use the published dashboard and screenshots.

## Data decisions and limitations

- Remove only the verified 22,523-row copy shared by the two source sheets. Keep
  other repeated records and large wholesale orders; classify credits and adjustments.
- Keep sales that meet the purchase rules in sales totals even when customer IDs
  are missing, but exclude those records from customer analysis. Compare
  January-November 2010 with the same months in 2011.
- Count a repeat only when a customer buys on a later calendar day. Use customers
  with a full 90 days of follow-up for all 30/60/90-day rates. Leave rates blank
  when follow-up cannot be observed, including groups where no customers have
  enough follow-up.
- Define customer groups using only purchases through 31 August 2011, then measure
  later purchases separately over 1 September-29 November. Include customers with
  no later purchases when calculating group rates. Do not change the group rules
  to make later results look more different.

These are historical UK retail records. Credits do not prove that goods were
physically returned. A customer's first recorded purchase may not be their first
ever purchase, and inactivity does not prove they have stopped buying permanently.
Later purchase results had already been viewed, so this is not a test on previously
unseen results or an experiment that proves cause and effect. Gross sales are not
profit, and growth in average invoice value does not show how the typical customer's
spending changed. These records cannot show the financial return on marketing or
a customer's true lifetime value. See
[methodology](docs/methodology.md) and [metric definitions](docs/metrics.md).

## Run the checks

Use Python 3.12 in a virtual environment, from the repository root:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python src/validate_analysis.py
python src/validate_analysis.py --from-source
```

The validator checks file hashes (fingerprints that detect changes), compares eight
saved exports with expected results, and checks three dashboard data files. The
`--from-source` option recalculates seven analyses in memory from the raw CSVs to
check the results without rewriting data or expected values. The 81 tests use sample inputs to check edge cases.
They are not findings about the retailer.
These local checks do not verify cloud query job details or dashboard interactions.
Inspecting a Tableau workbook requires a separate local copy.

See [reproduction instructions](docs/reproduction.md),
[validation results](reports/analysis/validation.json),
[cleaning controls](reports/analysis/cleaning_controls.json), and the
[dashboard specification](dashboard/specification.md).

## Dataset and license

This project uses the [Online Retail II dataset](https://archive.ics.uci.edu/dataset/502/online+retail+ii) from the UCI Machine Learning Repository, licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

**Citation:** Chen, D. (2012). *Online Retail II* [Dataset]. UCI Machine Learning Repository. [https://doi.org/10.24432/C5CG6D](https://doi.org/10.24432/C5CG6D)

The original workbook is kept unchanged. Its timestamps do not specify a timezone. Details about the source and prepared files are available in the [data notes](data/README.md) and [source manifest](reports/source_manifest.json).
