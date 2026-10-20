# Metric definitions

GBP amounts describe recorded merchandise purchasing, not audited revenue or
profit. Populations and date windows must be identical when comparing ratios.

| Metric | Definition |
|---|---|
| Gross merchandise sales | Sum of quantity x price for qualifying positive merchandise lines |
| Recorded merchandise credits | Absolute monetary value of classified negative merchandise entries, on recorded dates |
| Net recorded merchandise sales | Gross less recorded credits; unresolved adjustments stay separate |
| Purchase invoices | Distinct qualifying non-cancellation invoice numbers |
| Average invoice value | Gross merchandise sales / qualifying purchase invoices |
| Active identified customers | Distinct nonmissing IDs with qualifying purchases in the period |
| Invoices per identified customer | Identified qualifying invoices / active identified customers |
| Customer-ID sales coverage | Identified qualifying gross / all qualifying gross |
| Eligible first-observed customer | First observed qualifying date + 90 days <= 30 November 2011 |
| 30/60/90-day repeat rate | Eligible customers purchasing on a later day within the stated window / all fully 90-day-eligible customers |
| Conditional first-repeat timing | Median days to the first later-day purchase among eligible customers repeating within 90 days |
| Subsequent purchase rate | Snapshot customers purchasing in 1 September-29 November / all customers in that group |
| Customer share | Group customers / all 5,224 snapshot customers |
| Subsequent gross share | Group subsequent gross / total subsequent gross across all five base groups |
| Subsequent gross per customer | Group subsequent gross / all group customers, including non-purchasers |

The snapshot rate uses a common calendar window; repeat rates use a window
relative to each customer's first observed purchase. They answer different
questions. Ineligible repeat outcomes are blank, whereas snapshot non-purchasers
have FALSE/zero outcomes. A blank eligible denominator is not a zero rate.

All money sums retain their source precision. Ratio exports use nine decimal
places with half-away-from-zero rounding; dashboard display rounding is separate.
Counts are exact integers and identifiers are exact strings. See
[methodology](methodology.md) for boundaries, grains and eligibility rules.
