"""Prepare unchanged source-line CSVs and investigate cleaning evidence locally."""

import csv
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl
import pandas as pd

from profile_source import FIELDS, cross_sheet_matches, money_sum, read_sheet
from verify_source import sha256


NULL_MARKER = "__NULL__"
RENAME = {"Invoice": "invoice_no", "StockCode": "stock_code_raw", "Description": "description_raw", "Quantity": "quantity", "InvoiceDate": "invoice_datetime", "Price": "unit_price", "Customer ID": "customer_id", "Country": "country"}
RAW_COLUMNS = list(RENAME.values()) + ["source_sheet", "source_row", "description_source_type"]


def source_summary(frame):
    return {"rows": len(frame), "invoices": int(frame["Invoice"].nunique()), "identified_customers": int(frame["Customer ID"].nunique()), "missing_customer_id_rows": int(frame["Customer ID"].isna().sum()), "signed_amount_gbp": str(money_sum(frame["line_amount"])), "positive_amount_gbp": str(money_sum(frame.loc[frame["line_amount"] > 0, "line_amount"])), "negative_amount_gbp": str(money_sum(frame.loc[frame["line_amount"] < 0, "line_amount"]))}


def main():
    output = Path("data/processed")
    output.mkdir(parents=True, exist_ok=True)
    reports = Path("reports")
    manifest = json.loads((reports / "source_manifest.json").read_text(encoding="utf-8"))
    source_profile = json.loads((reports / "source_profile.json").read_text(encoding="utf-8"))
    path = Path(manifest["raw_path"])
    before = sha256(path)
    if before != manifest["sha256"] or before != source_profile["source_sha256"]:
        raise ValueError("Source checksum differs from the source profile.")
    frames = []
    file_controls = []
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
    try:
        for sheet in workbook:
            print(f"Reading {sheet.title} for raw load files...", flush=True)
            frame, _ = read_sheet(sheet)
            frame["description_source_type"] = frame["Description"].map(lambda x: "missing" if pd.isna(x) else "text" if isinstance(x, str) else "numeric")
            raw = frame.rename(columns=RENAME)[RAW_COLUMNS].copy()
            raw["description_raw"] = raw["description_raw"].astype("string")
            for column in raw.select_dtypes(include=["string", "object"]).columns:
                if raw[column].astype("string").eq(NULL_MARKER).any():
                    raise ValueError(f"Null marker collides with a source value in {column}.")
            raw["invoice_datetime"] = raw["invoice_datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
            for index, start in enumerate(range(0, len(raw), 200_000), start=1):
                target = output / f"raw_{sheet.title.replace(' ', '_')}_{index:02d}.csv"
                chunk = raw.iloc[start:start + 200_000]
                chunk.to_csv(target, index=False, na_rep=NULL_MARKER, lineterminator="\n")
                count = 0
                signed = Decimal("0")
                positive = Decimal("0")
                negative = Decimal("0")
                null_ids = 0
                with target.open(encoding="utf-8", newline="") as file:
                    reader = csv.DictReader(file)
                    if reader.fieldnames != RAW_COLUMNS:
                        raise ValueError("CSV field order differs from raw schema.")
                    for row in reader:
                        if None in row or any(value is None for value in row.values()):
                            raise ValueError("Malformed CSV record.")
                        amount = int(row["quantity"]) * Decimal(row["unit_price"])
                        signed += amount
                        positive += max(amount, Decimal("0"))
                        negative += min(amount, Decimal("0"))
                        null_ids += row["customer_id"] == NULL_MARKER
                        expected_row = frame.iloc[start + count]
                        if int(row["source_row"]) != expected_row["source_row"] or row["source_sheet"] != sheet.title:
                            raise ValueError("CSV source provenance changed.")
                        if row["description_source_type"] != expected_row["description_source_type"]:
                            raise ValueError("CSV description type flag changed.")
                        for original, field in RENAME.items():
                            value = expected_row[original]
                            expected = NULL_MARKER if pd.isna(value) else value.strftime("%Y-%m-%d %H:%M:%S") if original == "InvoiceDate" else str(value)
                            if row[field] != expected:
                                raise ValueError(f"CSV round-trip differs for {field} at {sheet.title}:{row['source_row']}")
                        count += 1
                original_chunk = frame.iloc[start:start + len(chunk)]
                expected = source_summary(original_chunk)
                if count != expected["rows"] or signed != Decimal(expected["signed_amount_gbp"]) or positive != Decimal(expected["positive_amount_gbp"]) or negative != Decimal(expected["negative_amount_gbp"]) or null_ids != expected["missing_customer_id_rows"]:
                    raise ValueError("CSV round-trip control mismatch.")
                file_controls.append({"path": target.as_posix(), "bytes": target.stat().st_size, "sha256": sha256(target), **expected})
                print(f"Verified {target.name}: {count:,} rows", flush=True)
            frames.append(frame)
    finally:
        workbook.close()
    raw_frame = pd.concat(frames, ignore_index=True)
    if raw_frame.duplicated(["source_sheet", "source_row"]).any():
        raise ValueError("Source-line provenance is not unique.")
    for frame, baseline in zip(frames, source_profile["sheets"]):
        summary = source_summary(frame)
        if summary["rows"] != baseline["rows"] or Decimal(summary["signed_amount_gbp"]) != Decimal(baseline["raw_signed_line_amount_gbp"]):
            raise ValueError("Import differs from accepted source controls.")
    overlap = cross_sheet_matches(frames[0], frames[1])
    if overlap["left_rows_in_overlap"] != overlap["matched_row_copies_min_multiplicity"] or overlap["right_rows_in_overlap"] != overlap["matched_row_copies_min_multiplicity"] or overlap["same_multiplicity_match_patterns"] != overlap["distinct_all_field_matches"]:
        raise ValueError("Overlap is no longer identical; review before applying sheet precedence.")
    excluded = raw_frame[raw_frame["source_sheet"].eq("Year 2009-2010") & raw_frame["InvoiceDate"].ge(pd.Timestamp("2010-12-01"))]
    canonical = raw_frame.drop(index=excluded.index).copy()
    if len(excluded) != overlap["matched_row_copies_min_multiplicity"]:
        raise ValueError("Sheet precedence would remove rows outside the verified overlap.")
    excluded[["source_sheet", "source_row", "Invoice", "StockCode", "line_amount"]].assign(exclusion_reason="overlapping_earlier_extract").to_csv(output / "overlap_exclusions.csv", index=False)
    canonical["stock_code"] = canonical["StockCode"].str.strip().str.upper()
    canonical["description"] = canonical["Description"].astype("string").str.strip()
    code_review = canonical.groupby("stock_code", dropna=False).agg(rows=("Invoice", "size"), positive_price_rows=("Price", lambda x: int((x > 0).sum())), signed_amount_gbp=("line_amount", money_sum), description_examples=("description", lambda x: " / ".join(sorted(set(x.dropna()))[:12]))).reset_index()
    code_review.to_csv(reports / "code_review.csv", index=False)
    suspicious = canonical[canonical["description_source_type"].ne("text") | canonical["description"].str.contains(r"(?i)^(?:check|checked|damag|fault|lost|missing|found|adjust|amazon|ebay|update|incorrect|wrong|given|sold|stock|historic|\?)", regex=True, na=False)]
    suspicious.groupby(["stock_code", "description", "description_source_type"], dropna=False).agg(rows=("Invoice", "size"), quantity_min=("Quantity", "min"), quantity_max=("Quantity", "max"), price_min=("Price", "min"), price_max=("Price", "max"), signed_amount_gbp=("line_amount", money_sum)).reset_index().to_csv(reports / "description_review.csv", index=False)
    invoices = canonical.groupby("Invoice").agg(customer_ids=("Customer ID", "nunique"), countries=("Country", "nunique"), timestamp_min=("InvoiceDate", "min"), timestamp_max=("InvoiceDate", "max"), timestamps=("InvoiceDate", "nunique"), calendar_days=("InvoiceDate", lambda x: x.dt.date.nunique()))
    invoices[invoices["timestamps"] > 1].to_csv(reports / "invoice_timestamp_review.csv")
    if before != sha256(path):
        raise ValueError("Raw workbook changed during preparation.")
    result = {"generated_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(), "source_sha256": before, "null_marker": NULL_MARKER, "raw_columns": RAW_COLUMNS, "raw_files": file_controls, "raw": source_summary(raw_frame), "overlap_excluded": source_summary(excluded), "canonical": source_summary(canonical), "canonical_exact_repeat_excess_rows_retained": int(canonical.duplicated(FIELDS).sum()), "canonical_invoices_multiple_calendar_days": int((invoices["calendar_days"] > 1).sum()), "overlap_verification": overlap, "verification": {"all_raw_csv_values_round_trip": True, "source_unchanged": True, "unique_source_provenance": True, "source_profile_reconciled": True, "overlap_multiplicities_match": True}}
    (reports / "load_manifest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("Raw load files and cleaning evidence prepared; no cloud operations performed.", flush=True)


if __name__ == "__main__":
    main()
