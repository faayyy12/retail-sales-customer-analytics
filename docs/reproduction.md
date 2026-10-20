# Reproduce and validate the analysis

Clone the public repository and enter its directory:

```sh
git clone https://github.com/faayyy12/retail-sales-customer-analytics.git
cd retail-sales-customer-analytics
```

Use Python 3.12 and a project virtual environment. Install the pinned dependencies
from `requirements.txt`; no cloud credentials are needed for the checks below.
On Windows, create the environment with `python -m venv .venv` and use
`.venv\Scripts\python.exe` in place of `python`. On Unix use `.venv/bin/python`.
Run commands from the repository root.

```sh
python -m pip install -r requirements.txt
python -m pip check
python -m unittest discover -s tests -v
python src/validate_analysis.py
python src/validate_analysis.py --from-source
```

The default validator checks 48 public input checksums, eight saved-export
comparisons and exact reproduction of three Tableau input CSVs. It compares
schemas, keys, Decimal money, integer counts, dates, booleans and NULL semantics.
The source option also independently reconstructs seven analyses in memory from
the six raw CSVs, then compares them with the unchanged expected CSVs. It checks
that removing future records leaves snapshot features unchanged. These commands
do not overwrite source data, expected results or dashboard inputs.

An optional `--output` writes a compact numerical validation summary and refuses
to overwrite the registered inputs. [Input checksums](../reports/analysis/input_checksums.json)
cover data, schemas, fixed rules and screenshots; they are not hashes of mutable
source code or a claim of cloud execution. [Validation results](../reports/analysis/validation.json)
and [analytical controls](../reports/analysis/summary.json) accompany the findings.
The synthetic tests exercise edge cases and are separate from retailer evidence.

## Workflow and files

| Stage | Implementation | Inputs / outputs |
|---|---|---|
| Source and typing | `verify_source.py`, `profile_source.py`, `prepare_load.py` | Unchanged UCI workbook, sheet/row provenance, six raw CSVs and overlap audit |
| Cleaning | `cleaning_rules.py`, `reconcile_cleaning.py`; SQL 01-04 | Canonical history, evidence-based code rules, clean lines and qualifying invoices |
| Sales and credits | SQL 05-07; corresponding Python aggregators | Monthly/period sales, identified/anonymous factors and credit concentrations |
| Repeat purchasing | SQL 08-09; customer-history and cohort functions | First-observed customer histories, equal-window rates and conditional timing |
| Customer priorities | SQL 10-11; snapshot and segment functions | Historical-only features, later outcomes, fixed groups and sensitivity settings |
| Presentation | `prepare_presentation.py` calculation functions | Two sales-period rows, five base rows and separate sensitivity rows |

The consolidated validator is the public command-line interface for saved outputs
and source reconstruction. Calculation functions are reusable from the modules
above. Individual stage wrappers can require additional local control records;
they are not prerequisites for the advertised public checks.

## BigQuery SQL

Use the ordered GoogleSQL templates in `sql/`, the explicit schemas in `config/`,
and the [metric definitions](metrics.md). Copy `config/bigquery.example.json` to
`config/bigquery.local.json`, replace placeholders with your own dedicated project,
dataset and location, and render locally:

```sh
python src/render_sql.py --config config/bigquery.local.json
```

Rendered SQL and local configuration are excluded from Git. Rendering submits
no query. Run source/import and join-key checks before consuming derived tables;
compare exported results with the included expected CSVs. The public validation
commands use saved exports and never submit BigQuery SQL. Inspect byte estimates
and set a suitable query ceiling in your own console; configuration alone does
not apply console settings. Historical cloud job identifiers and billing details
are not supplied, so local comparisons do not verify those metadata.

## Tableau

Use the [published dashboard](https://public.tableau.com/app/profile/fab.li/viz/RetailSalesCustomerAnalytics/CustomerPriorities)
and the [public specification](../dashboard/specification.md). Connect the four
documented CSV grains as separate data sources when rebuilding in Tableau.
The downloadable Tableau workbook and extracts are not included. Package
inspection and opening/interaction tests require a separately available local
workbook and Tableau; they are not fresh-clone checks. CSV reconciliation and
screenshots alone do not prove interactive runtime behavior.
