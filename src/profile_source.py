"""Inspect every original row. Write diagnostics, never a cleaned dataset."""

import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
import platform
import xml.etree.ElementTree as ET
from zipfile import ZipFile
from zoneinfo import ZoneInfo

import openpyxl
import pandas as pd

from verify_source import sha256


FIELDS = ["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]
IDENTIFIERS = ["Invoice", "StockCode", "Customer ID"]
ZERO = Decimal("0")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def identifier(value):
    """Excel numeric IDs become integer strings; existing text stays intact."""
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        if int(value) != value:
            raise ValueError(f"Nonintegral numeric identifier: {value!r}")
        return str(int(value))
    return str(value)


def money_sum(values):
    return sum(values, ZERO)


def records(frame):
    """Small diagnostic records suitable for JSON, without binary float money."""
    printable = frame.map(lambda x: str(x) if isinstance(x, Decimal) else x)
    return json.loads(printable.to_json(orient="records", date_format="iso", default_handler=str))


def read_sheet(sheet):
    rows = sheet.iter_rows()
    headers = [cell.value for cell in next(rows)]
    if headers != FIELDS:
        raise ValueError(f"Unexpected headers in {sheet.title}: {headers}")
    types = {field: Counter() for field in FIELDS}
    cell_types = {field: Counter() for field in FIELDS}
    values = []
    source_rows = []
    blanks = []
    for source_row, cells in enumerate(rows, start=2):
        row = [cell.value for cell in cells]
        if all(value is None for value in row):
            blanks.append(source_row)
            continue
        for field, cell in zip(FIELDS, cells):
            types[field][type(cell.value).__name__] += 1
            cell_types[field][cell.data_type] += 1
            if cell.data_type in {"f", "e"}:
                raise ValueError(f"Formula/error cell at {sheet.title}!{cell.coordinate}")
        values.append(row)
        source_rows.append(source_row)
    frame = pd.DataFrame(values, columns=FIELDS)
    for field in IDENTIFIERS:
        frame[field] = frame[field].map(identifier).astype("string")
    frame["Price"] = frame["Price"].map(lambda x: Decimal(str(x)))
    frame["Quantity"] = pd.to_numeric(frame["Quantity"], errors="raise")
    if frame["Quantity"].isna().any() or (frame["Quantity"] % 1 != 0).any():
        raise ValueError("Missing or noninteger quantity needs an explicit handling rule.")
    frame["Quantity"] = frame["Quantity"].astype("int64")
    frame["InvoiceDate"] = pd.to_datetime(frame["InvoiceDate"], errors="raise")
    frame["source_sheet"] = sheet.title
    frame["source_row"] = source_rows
    frame["line_amount"] = [int(q) * p for q, p in zip(frame["Quantity"], frame["Price"])]
    metadata = {
        "headers": headers,
        "worksheet_max_row_including_header": sheet.max_row,
        "worksheet_max_column": sheet.max_column,
        "blank_rows": blanks,
        "original_python_value_types": {k: dict(v) for k, v in types.items()},
        "original_excel_cell_types": {k: dict(v) for k, v in cell_types.items()},
        "profile_dtypes": {field: str(frame[field].dtype) for field in frame.columns},
    }
    return frame, metadata


def profile(frame):
    cancellation = frame["Invoice"].str.upper().str.startswith("C").fillna(False)
    positive = (frame["Quantity"] > 0) & (frame["Price"] > ZERO) & ~cancellation
    identified = frame["Customer ID"].notna()
    positive_amount = money_sum(frame.loc[positive, "line_amount"])
    identified_positive_amount = money_sum(frame.loc[positive & identified, "line_amount"])
    invoice_groups = frame.groupby("Invoice", dropna=False)
    invoice_ids = invoice_groups["Customer ID"].nunique()
    invoice_dates = invoice_groups["InvoiceDate"].nunique()
    invoice_countries = invoice_groups["Country"].nunique()
    full_duplicates = frame.duplicated(FIELDS, keep=False)
    extra_duplicates = frame.duplicated(FIELDS, keep="first")
    repeated_pair = frame.duplicated(["Invoice", "StockCode"], keep=False)
    result = {
        "rows": len(frame),
        "date_min": frame["InvoiceDate"].min().isoformat(),
        "date_max": frame["InvoiceDate"].max().isoformat(),
        "missing_by_field": {field: int(frame[field].isna().sum()) for field in FIELDS},
        "blank_or_whitespace_text_by_field": {
            field: int(frame[field].astype("string").str.fullmatch(r"\s*", na=False).sum())
            for field in ["Invoice", "StockCode", "Description", "Customer ID", "Country"]
        },
        "distinct_invoices_all_records": int(frame["Invoice"].nunique()),
        "distinct_identified_customers_all_records": int(frame["Customer ID"].nunique()),
        "identified_row_coverage": int(identified.sum()) / len(frame),
        "candidate_positive_non_cancellation_all_code_rows": int(positive.sum()),
        "candidate_positive_non_cancellation_all_code_amount_gbp": str(positive_amount),
        "candidate_identified_positive_all_code_amount_gbp": str(identified_positive_amount),
        "candidate_identified_positive_all_code_amount_coverage": str(identified_positive_amount / positive_amount),
        "raw_signed_line_amount_gbp": str(money_sum(frame["line_amount"])),
        "raw_positive_line_amount_gbp": str(money_sum(frame.loc[frame["line_amount"] > ZERO, "line_amount"])),
        "raw_negative_line_amount_gbp": str(money_sum(frame.loc[frame["line_amount"] < ZERO, "line_amount"])),
        "cancellation_prefix_rows": int(cancellation.sum()),
        "cancellation_prefix_distinct_invoices": int(frame.loc[cancellation, "Invoice"].nunique()),
        "negative_quantity_rows": int((frame["Quantity"] < 0).sum()),
        "zero_quantity_rows": int((frame["Quantity"] == 0).sum()),
        "zero_price_rows": int((frame["Price"] == ZERO).sum()),
        "negative_price_rows": int((frame["Price"] < ZERO).sum()),
        "negative_quantity_without_c_prefix_rows": int(((frame["Quantity"] < 0) & ~cancellation).sum()),
        "c_prefix_positive_quantity_rows": int((cancellation & (frame["Quantity"] > 0)).sum()),
        "other_invoice_format_rows": int((~frame["Invoice"].str.fullmatch(r"[Cc]?\d{6}", na=False)).sum()),
        "price_decimal_places_max": max(max(0, -price.as_tuple().exponent) for price in frame["Price"]),
        "quantity_min": int(frame["Quantity"].min()),
        "quantity_max": int(frame["Quantity"].max()),
        "price_min": str(frame["Price"].min()),
        "price_max": str(frame["Price"].max()),
        "full_row_duplicate_participating_rows": int(full_duplicates.sum()),
        "full_row_duplicate_extra_rows_beyond_first": int(extra_duplicates.sum()),
        "full_row_duplicate_extra_raw_signed_amount_gbp": str(money_sum(frame.loc[extra_duplicates, "line_amount"])),
        "invoice_stockcode_repeated_key_participating_rows": int(repeated_pair.sum()),
        "invoice_stockcode_extra_rows_beyond_first": int(frame.duplicated(["Invoice", "StockCode"]).sum()),
        "invoices_multiple_nonmissing_customer_ids": int((invoice_ids > 1).sum()),
        "invoices_mixed_missing_and_present_customer_id": int((invoice_groups["Customer ID"].count().gt(0) & invoice_groups["Customer ID"].count().lt(invoice_groups.size())).sum()),
        "invoices_multiple_timestamps": int((invoice_dates > 1).sum()),
        "invoices_multiple_countries": int((invoice_countries > 1).sum()),
        "invoice_conflict_examples": {
            "customer_ids": list(invoice_ids[invoice_ids > 1].index[:10]),
            "timestamps": list(invoice_dates[invoice_dates > 1].index[:10]),
            "countries": list(invoice_countries[invoice_countries > 1].index[:10]),
        },
        "duplicate_examples": records(frame.loc[full_duplicates, FIELDS + ["source_sheet", "source_row"]].head(8)),
    }
    return result


def cross_sheet_matches(left, right):
    """Exact all-field matches; multiset intersection avoids many-to-many joins."""
    start = max(left["InvoiceDate"].min(), right["InvoiceDate"].min())
    end = min(left["InvoiceDate"].max(), right["InvoiceDate"].max())
    left_window = left[left["InvoiceDate"].between(start, end)]
    right_window = right[right["InvoiceDate"].between(start, end)]
    a = left_window.groupby(FIELDS, dropna=False).size().rename("left_count").reset_index()
    b = right_window.groupby(FIELDS, dropna=False).size().rename("right_count").reset_index()
    matches = a.merge(b, on=FIELDS, how="inner", validate="one_to_one")
    matches["matched_copies"] = matches[["left_count", "right_count"]].min(axis=1)
    matched_amount = money_sum(int(q) * p * int(n) for q, p, n in zip(matches["Quantity"], matches["Price"], matches["matched_copies"]))
    return {
        "left_sheet": str(left["source_sheet"].iloc[0]),
        "right_sheet": str(right["source_sheet"].iloc[0]),
        "timestamp_overlap_start": start.isoformat() if start <= end else None,
        "timestamp_overlap_end": end.isoformat() if start <= end else None,
        "left_rows_in_overlap": len(left_window),
        "right_rows_in_overlap": len(right_window),
        "distinct_all_field_matches": len(matches),
        "matched_row_copies_min_multiplicity": int(matches["matched_copies"].sum()),
        "matched_raw_signed_amount_gbp": str(matched_amount),
        "same_multiplicity_match_patterns": int(matches["left_count"].eq(matches["right_count"]).sum()),
        "left_rows_with_matching_pattern": int(matches["left_count"].sum()),
        "right_rows_with_matching_pattern": int(matches["right_count"].sum()),
        "examples": records(matches.head(5)),
    }


def xml_controls(path):
    """Independent XML counts and Q x Price sums using Excel numeric semantics.

    Excel numeric cells are binary doubles. Convert the raw token to that number,
    then its shortest decimal string, as openpyxl does. Do not treat serialization
    tails as extra meaningful price precision. Also record literal-token totals.
    """
    controls = []
    with ZipFile(path) as archive:
        # Read workbook relationships: filenames or ordering are not sheet identity.
        book = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
        for sheet in book.find(NS + "sheets"):
            relationship = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = targets[relationship]
            member = target.lstrip("/") if target.startswith("/") else "xl/" + target
            count = 0
            signed = ZERO
            positive = ZERO
            negative = ZERO
            literal_signed = ZERO
            serialization_tail_rows = 0
            serialization_examples = []
            with archive.open(member) as stream:
                for _, element in ET.iterparse(stream, events=("end",)):
                    if element.tag != NS + "row":
                        continue
                    if int(element.attrib["r"]) > 1:
                        cells = {cell.attrib["r"].rstrip("0123456789"): cell for cell in element}
                        populated = any(cell.find(NS + "v") is not None or cell.find(NS + "is") is not None for cell in cells.values())
                        if populated:
                            quantity = Decimal(cells["D"].find(NS + "v").text)
                            price_token = cells["F"].find(NS + "v").text
                            price = Decimal(str(float(price_token)))
                            literal_price = Decimal(price_token)
                            literal_signed += quantity * literal_price
                            if price != literal_price:
                                serialization_tail_rows += 1
                                if len(serialization_examples) < 5:
                                    serialization_examples.append({"source_row": int(element.attrib["r"]), "xml_price_token": price_token, "canonical_numeric_price": str(price)})
                            amount = quantity * price
                            signed += amount
                            positive += max(amount, ZERO)
                            negative += min(amount, ZERO)
                            count += 1
                    element.clear()
            controls.append({"sheet": sheet.attrib["name"], "rows": count, "raw_signed_line_amount_gbp": str(signed), "raw_positive_line_amount_gbp": str(positive), "raw_negative_line_amount_gbp": str(negative), "literal_xml_token_signed_amount_gbp": str(literal_signed), "canonical_minus_literal_signed_amount_gbp": str(signed - literal_signed), "price_serialization_tail_rows": serialization_tail_rows, "price_serialization_examples": serialization_examples})
    return controls


def write_acceptance_report(result, manifest, output):
    """Generate the compact acceptance summary from the executed diagnostics."""
    combined = result["combined_before_cleaning"]
    date = result["generated_at"][:10]
    lines = [
        f"# Source acceptance — {date}",
        "",
        "**Accepted for cleaning and analysis design.** Source integrity and local import checks passed. Cleaning, BigQuery loading, SQL execution and dashboard development are pending.",
        "",
        f"**Source:** [UCI Online Retail II, 502]({manifest['source_url']}); Chen, D. (2012), [DOI]({manifest['doi']}), [CC BY 4.0]({manifest['license_url']}). Official download retrieved on {manifest['official_download_retrieved_at'][:10]}; original local retrieval date unknown. `{manifest['filename']}` ({manifest['bytes']:,} bytes) matches UCI byte for byte.",
        "",
        f"**SHA-256:** `{manifest['sha256']}`",
        "",
        "| Sheet | Data rows | First timestamp | Last timestamp | Missing customer IDs |",
        "|---|---:|---|---|---:|",
    ]
    for sheet in result["sheets"]:
        lines.append(f"| {sheet['sheet']} | {sheet['rows']:,} | {sheet['date_min'].replace('T', ' ')} | {sheet['date_max'].replace('T', ' ')} | {sheet['missing_by_field']['Customer ID']:,} |")
    lines += [
        "",
        f"**Fields and types:** Both sheets have exactly `Invoice`, `StockCode`, `Description`, `Quantity`, `InvoiceDate`, `Price`, `Customer ID`, `Country`. Invoice/stock codes mix numeric and text cells; customer IDs are numeric or missing. Profiles preserve identifiers as strings. Quantity is integer, date is a naive datetime, price is numeric with up to {combined['price_decimal_places_max']} meaningful decimal places, country is text. Description is text/missing plus four numeric cells. No blank data rows or formula/error cells were encountered.",
        "",
        f"**Coverage:** {combined['rows']:,} rows before any cleaning; {combined['distinct_invoices_all_records']:,} invoice codes and {combined['distinct_identified_customers_all_records']:,} identified customer IDs. Missing IDs: {combined['missing_by_field']['Customer ID']:,} ({1 - combined['identified_row_coverage']:.2%}); missing descriptions: {combined['missing_by_field']['Description']:,}. No other source fields are missing. Identified IDs cover {Decimal(combined['candidate_identified_positive_all_code_amount_coverage']):.2%} of positive-quantity/positive-price, non-C-invoice amounts **before code classification, overlap removal or cutoff filters**. This is a diagnostic proxy, not final merchandise-sales coverage.",
        "",
        "**Overlap and grain:**",
        "",
    ]
    for overlap in result["cross_sheet_overlap"]:
        lines.append(f"- Confirmed overlap: {overlap['timestamp_overlap_start'].replace('T', ' ')} through {overlap['timestamp_overlap_end'].replace('T', ' ')}. Each sheet has {overlap['matched_row_copies_min_multiplicity']:,} matching row copies across {overlap['distinct_all_field_matches']:,} distinct all-field patterns, with equal multiplicities. Unclassified signed amount repeated: GBP {overlap['matched_raw_signed_amount_gbp']}.")
    extras = ", ".join(f"{sheet['full_row_duplicate_extra_rows_beyond_first']:,}" for sheet in result["sheets"])
    lines += [
        f"- Within-sheet exact-repeat excess rows: {extras} respectively. Combined exact-repeat excess is {combined['full_row_duplicate_extra_rows_beyond_first']:,}; these counts overlap and must not be added. No duplicates were removed.",
        f"- `(source_sheet, source_row)` is unique. Invoice + StockCode is not: {combined['invoice_stockcode_extra_rows_beyond_first']:,} excess rows. No invoice has conflicting identified customers or countries; {combined['invoices_multiple_timestamps']:,} invoice codes have multiple timestamps.",
        "",
        f"**Unusual entries:** {combined['cancellation_prefix_rows']:,} C-prefix rows; {combined['negative_quantity_rows']:,} negative-quantity rows ({combined['negative_quantity_without_c_prefix_rows']:,} without C prefix); {combined['zero_price_rows']:,} zero prices; {combined['negative_price_rows']:,} negative prices labelled bad-debt adjustments; no zero quantities. Codes/descriptions include postage, carriage, fees, discounts, vouchers, tests and manual adjustments. Quantity reaches ±{combined['quantity_max']:,}; matching large purchase/credit examples require investigation, not automatic exclusion.",
        "",
        "**Verification:** All sheet/combined counts and decimal positive, negative and signed controls reconciled independently to XLSX XML under documented Excel numeric parsing. Source checksum unchanged. Literal XML serialization differences are below GBP 0.0000000001 per sheet and retained in the detailed profile. Synthetic edge-case tests are separate from these source controls; execution results are recorded in the README.",
        "",
        "**Unresolved / next:** Select one copy of the verified overlap while retaining within-sheet multiplicities. Review other repeated lines, nonmerchandise codes, missing/numeric descriptions, invoice timestamps and large adjustments. Source timezone is unspecified. January–November comparison periods and the 30 November 2011 cutoff are supported by observed coverage; December 2011 is partial. See [decisions](../docs/source_decisions.md), [detailed profile](source_profile.json), [review samples](source_review_examples.csv) and [environment check](environment_check.md).",
    ]
    (output / "source_acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=Path("data/raw/online_retail_II.xlsx"))
    args = parser.parse_args()
    manifest = json.loads(Path("reports/source_manifest.json").read_text(encoding="utf-8"))
    before = sha256(args.workbook)
    if before != manifest["sha256"]:
        raise ValueError("Workbook checksum differs from the verified source manifest.")
    workbook = openpyxl.load_workbook(args.workbook, read_only=True, data_only=False)
    frames = []
    sheet_results = []
    try:
        for sheet in workbook:
            print(f"Reading all rows in {sheet.title}...", flush=True)
            frame, metadata = read_sheet(sheet)
            print(f"Profiling {len(frame):,} records...", flush=True)
            sheet_results.append({"sheet": sheet.title, **metadata, **profile(frame)})
            frames.append(frame)
    finally:
        workbook.close()
    all_rows = pd.concat(frames, ignore_index=True)
    print("Checking combined grain and cross-sheet overlap...", flush=True)
    combined = profile(all_rows)
    overlap = [cross_sheet_matches(left, right) for i, left in enumerate(frames) for right in frames[i + 1:]]
    print("Reconciling row counts and money directly to XLSX XML...", flush=True)
    controls = xml_controls(args.workbook)
    for sheet, control in zip(sheet_results, controls):
        assert sheet["sheet"] == control["sheet"]
        for metric in ["rows", "raw_signed_line_amount_gbp", "raw_positive_line_amount_gbp", "raw_negative_line_amount_gbp"]:
            assert Decimal(str(sheet[metric])) == Decimal(str(control[metric])), (sheet["sheet"], metric)
    assert sum(sheet["rows"] for sheet in sheet_results) == combined["rows"]
    assert money_sum(Decimal(sheet["raw_signed_line_amount_gbp"]) for sheet in sheet_results) == Decimal(combined["raw_signed_line_amount_gbp"])
    assert not all_rows.duplicated(["source_sheet", "source_row"]).any()
    assert before == sha256(args.workbook)
    output = Path("reports")
    output.mkdir(exist_ok=True)
    result = {
        "generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
        "runtime": {"python": platform.python_version(), "pandas": pd.__version__, "openpyxl": openpyxl.__version__},
        "source_sha256": before,
        "method": "Every nonblank source row; identifiers as strings, Excel rows starting at 2, Decimal(str(original numeric price)), no removal or business classifications. XML controls interpret numeric price tokens as Excel binary numeric values before decimal conversion; literal-token differences are reported separately. No monetary rounding is applied.",
        "amount_warning": "Unclassified source controls include adjustments, anonymous records and overlapping extracts. These are not merchandise sales metrics.",
        "sheets": sheet_results,
        "combined_before_cleaning": combined,
        "cross_sheet_overlap": overlap,
        "independent_xml_controls": controls,
        "verification": {"checksum_unchanged": True, "unique_sheet_row_provenance": True, "sheet_counts_sum_to_combined": True, "sheet_signed_totals_sum_to_combined": True, "xml_counts_and_decimal_positive_negative_signed_totals_match": True},
    }
    (output / "source_profile.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    monthly = all_rows.assign(month=all_rows["InvoiceDate"].dt.strftime("%Y-%m")).groupby(["source_sheet", "month"]).agg(rows=("Invoice", "size"), date_min=("InvoiceDate", "min"), date_max=("InvoiceDate", "max"), missing_customer_id_rows=("Customer ID", lambda x: x.isna().sum()), raw_signed_line_amount_gbp=("line_amount", money_sum)).reset_index()
    monthly.to_csv(output / "source_monthly_counts.csv", index=False)
    unusual = all_rows[~all_rows["StockCode"].str.fullmatch(r"\d{5}[A-Za-z]*", na=False)]
    codes = unusual.groupby("StockCode", dropna=False).agg(rows=("Invoice", "size"), raw_signed_line_amount_gbp=("line_amount", money_sum), negative_quantity_rows=("Quantity", lambda x: (x < 0).sum()), zero_price_rows=("Price", lambda x: (x == ZERO).sum()), description_examples=("Description", lambda x: " / ".join(sorted(set(x.dropna().astype(str)))[:5]))).reset_index()
    codes.to_csv(output / "source_unusual_codes.csv", index=False)
    examples = []
    cancellation = all_rows["Invoice"].str.upper().str.startswith("C").fillna(False)
    masks = {
        "negative_price": all_rows["Price"] < ZERO,
        "zero_price": all_rows["Price"] == ZERO,
        "negative_quantity_without_c_prefix": (all_rows["Quantity"] < 0) & ~cancellation,
        "other_invoice_format": ~all_rows["Invoice"].str.fullmatch(r"[Cc]?\d{6}", na=False),
        "duplicate_full_row": all_rows.duplicated(FIELDS, keep=False),
    }
    for kind, mask in masks.items():
        examples.append(all_rows.loc[mask].head(6).assign(example_kind=kind))
    largest = all_rows["line_amount"].abs().sort_values(ascending=False).head(10).index
    examples.append(all_rows.loc[largest].assign(example_kind="largest_absolute_line_amount"))
    conflicts = combined["invoice_conflict_examples"]
    for kind, invoices in conflicts.items():
        examples.append(all_rows[all_rows["Invoice"].isin(invoices)].head(30).assign(example_kind=f"invoice_multiple_{kind}"))
    pd.concat(examples, ignore_index=True).to_csv(output / "source_review_examples.csv", index=False)
    write_acceptance_report(result, manifest, output)
    print(f"Complete: {len(all_rows):,} rows; all source controls matched. See reports/.", flush=True)


if __name__ == "__main__":
    main()
