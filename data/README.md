# Dataset attribution and redistribution

The original workbook is `raw/online_retail_II.xlsx`, verified unchanged against the official [UCI Online Retail II dataset (502)](https://archive.ics.uci.edu/dataset/502/online+retail+ii).

**Attribution:** Chen, D. (2012). *Online Retail II* [Dataset]. UCI Machine Learning Repository. [https://doi.org/10.24432/C5CG6D](https://doi.org/10.24432/C5CG6D).

**Dataset license:** [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/), as recorded by UCI. This attribution also applies to the included public-data extracts and diagnostic outputs. No affiliation with or endorsement by the dataset creator is implied.

**Changes in derived files:** The six `processed/raw_*.csv` files convert Excel rows to UTF-8 CSV, standardize identifier/date/decimal representations, add source-sheet/source-row references and original description type flags, and represent missing values with `__NULL__`. They retain every original data row, including the overlapping extracts. `processed/overlap_exclusions.csv` lists the earlier-sheet overlap rows excluded from canonical analytical history. Reports and controls summarize these data under documented analytical rules. The source workbook itself is unchanged.

Provenance, retrieval evidence and license information are in [source_manifest.json](../reports/source_manifest.json). [load_manifest.json](../reports/load_manifest.json) records sizes and SHA-256 values for the six CSVs. The files are committed directly in Git so an ordinary clone includes them. `.gitattributes` prevents newline conversions from changing their bytes.

These are historical public retail records, not current customer data or independently integrated business systems. Dataset licensing does not assign a software license to the project's code.
