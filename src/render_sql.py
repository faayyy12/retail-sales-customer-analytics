"""Substitute validated project/dataset placeholders; never submit cloud queries."""

import argparse
import json
from pathlib import Path
import re


def render(sql, config):
    project = config["project_id"]
    dataset = config["dataset_id"]
    if project.startswith("YOUR_") or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
        raise ValueError("Provide a real Google Cloud project ID, not a display name.")
    if dataset.startswith("YOUR_") or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,1023}", dataset):
        raise ValueError("Provide a dedicated dataset ID.")
    location = config.get("location", "")
    if not location or location.startswith("YOUR_"):
        raise ValueError("Set the actual dataset location before loading or querying.")
    if config.get("account_mode") not in {"sandbox", "billing_enabled"}:
        raise ValueError("Confirm account_mode as sandbox or billing_enabled; this script never enables billing.")
    if not isinstance(config.get("maximum_bytes_billed"), int) or config["maximum_bytes_billed"] <= 0:
        raise ValueError("Set a positive maximum_bytes_billed ceiling.")
    return sql.replace("{{PROJECT_ID}}", project).replace("{{DATASET_ID}}", dataset)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    output = Path("data/processed/sql")
    rendered = [(source.name, render(source.read_text(encoding="utf-8"), config)) for source in sorted(Path("sql").glob("*.sql"))]
    output.mkdir(parents=True, exist_ok=True)
    for name, sql in rendered:
        (output / name).write_text(sql, encoding="utf-8")
    print(f"Rendered {len(rendered)} SQL files in {output}. No query was submitted.")


if __name__ == "__main__":
    main()
