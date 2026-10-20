# Retail revenue drivers and repeat purchasing

This historical UK giftware case study compares January-November 2010 and 2011,
uses full earlier observed purchasing history, and keeps wholesale activity and
anonymous qualifying purchases in the appropriate populations.

## Sales: higher mean invoice value offset fewer invoices

Gross merchandise sales rose **2.89%**, from **GBP 8,628,418.41** to
**GBP 8,878,008.02**. Invoices fell **3.71%** (18,077 to 17,407), while mean
invoice value rose **6.85%** (GBP 477.31 to GBP 510.03). The mean increase remains
6.23% after diagnostically separating each year's ten largest invoices, but the
median rises only 1.11%. Headline totals retain those large invoices. These
results do not establish uniform changes in typical-customer spending.

Identified gross fell GBP 2,462.61; gross without customer IDs rose
GBP 252,052.22. ID coverage fell from **89.04% to 86.51%**. Among each year's
identified population, active IDs rose 0.99%, invoices/customer fell 3.73% and
mean invoice value rose 2.82%; their unrounded multiplicative identity reconciles.
The annual populations are not a matched-customer panel.

Recorded credits rose **GBP 65,618.68**, reducing gross-less-credits growth to
**2.19%**. January, large credit invoices and stock code 23166 show overlapping
concentrations; these explanations cannot be added together. Credits do not
prove physical returns or explain their causes, and gross less credits is not
audited revenue or profit.

**Practical implication:** investigate missing-ID recording, material credit
documents and quantity/product/wholesale mix before attributing growth to pricing
or proposing an intervention.

## Repeat purchasing: use equal observation windows

Of **5,824** first-observed identified customers, **5,227** have complete 90-day
follow-up and **597** are ineligible. On the same eligible denominator,
**18.63% / 34.26% / 44.10%** repeat on a later calendar day within 30/60/90 days.
Among the **2,305** eligible 90-day repeaters, the pooled median first-repeat
gap is **36 days**. This conditional median is not an expected waiting time for
all customers or a recommended contact day.

First observed does not mean acquired. December 2009 lacks earlier history;
September 2011 has only three eligible customers. October-November have none,
so their rates remain blank. **Practical implication:** evaluate cadence and
customer mix before treating a short absence as churn.

## Historical groups: prioritize investigation, not promised response

Five fixed groups cover **5,224** identified customers observed by 31 August
2011. In 1 September-29 November, **2,236 (42.80%)** purchase subsequently,
generating **GBP 2,699,494.19** across 5,207 invoices. This common calendar-window
rate differs from the first-purchase-relative repeat rate above.

Recent higher-spend repeat customers comprise **17.53%** of customers and
**67.23%** of subsequent gross, with an **82.97%** purchase rate. Their gross
share stays between **65.06% and 69.26%** across six fixed one-at-a-time rule
variants. Less-recent multi-day customers purchase at **36.78%**, compared with
**16.57%** for less-recent single-day customers.

**Practical implication:** review service needs for the higher-spend repeat
group, observe customers with limited history separately, and investigate cadence
among less-recent customers. These proposals establish neither outreach response
nor incremental revenue. Outcomes had already been inspected, so this is not an
untouched holdout. Unequal observed histories, population mix and wholesale
concentration limit generalization.

Evidence: [numerical summaries](summary.json), [cleaning controls](cleaning_controls.json),
[saved-export and source comparisons](validation.json), and the unchanged CSV
inputs registered in [input checksums](input_checksums.json). Definitions and
boundaries are in [methodology](../../docs/methodology.md) and
[metrics](../../docs/metrics.md). These comparisons establish local numerical
agreement, not cloud job metadata, causal effects or current Tableau runtime.
