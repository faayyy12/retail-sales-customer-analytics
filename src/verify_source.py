"""Download UCI's archive and verify the existing workbook without altering it."""

import argparse
import hashlib
import io
import json
import shutil
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile
from zoneinfo import ZoneInfo


SOURCE_URL = "https://archive.ics.uci.edu/dataset/502/online+retail+ii"
DOWNLOAD_URL = "https://archive.ics.uci.edu/static/public/502/online%2Bretail%2Bii.zip"
FILENAME = "online_retail_II.xlsx"


def sha256(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", type=Path, default=Path(FILENAME))
    args = parser.parse_args()
    print("Downloading the official UCI archive for checksum verification...", flush=True)
    with urlopen(DOWNLOAD_URL, timeout=120) as response:
        archive_bytes = response.read()
    with ZipFile(io.BytesIO(archive_bytes)) as archive:
        workbook = archive.read(FILENAME)
    official_hash = hashlib.sha256(workbook).hexdigest()
    local_hash = sha256(args.local) if args.local.exists() else None
    if local_hash is not None and local_hash != official_hash:
        raise ValueError("Local workbook differs from UCI. No local file was overwritten.")
    destination = Path("data/raw") / FILENAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256(destination) != official_hash:
            raise ValueError("Existing raw workbook differs from UCI. No file was overwritten.")
    elif local_hash is not None:
        shutil.copyfile(args.local, destination)
    else:
        destination.write_bytes(workbook)
    assert sha256(destination) == official_hash
    manifest = {
        "dataset": "UCI Online Retail II (502)",
        "source_url": SOURCE_URL,
        "download_url": DOWNLOAD_URL,
        "official_download_retrieved_at": datetime.now(ZoneInfo("Asia/Taipei")).isoformat(),
        "existing_local_original_retrieval_date": None,
        "existing_local_original_retrieval_note": "Not known; filesystem times are not retrieval evidence.",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution": "Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository.",
        "doi": "https://doi.org/10.24432/C5CG6D",
        "filename": FILENAME,
        "raw_path": destination.as_posix(),
        "bytes": len(workbook),
        "sha256": official_hash,
        "official_archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "existing_local_sha256": local_hash,
        "verification": "Raw workbook is byte-identical to workbook in the official UCI archive.",
        "source_timestamp_timezone": "Unspecified by UCI; retain naive source datetimes.",
    }
    output = Path("reports/source_manifest.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Verified {destination}: {official_hash}", flush=True)


if __name__ == "__main__":
    main()
