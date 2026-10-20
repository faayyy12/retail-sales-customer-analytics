"""Small local reconciliation of the rules documented and implemented in GoogleSQL."""

from decimal import Decimal
from pathlib import Path
import re

import pandas as pd


ADJUSTMENT_DESCRIPTION = r"(?i)^(?:\?+|CHECK(?:ED)?|DAMAGED?|FAULTY|LOST|MISSING|FOUND|ADJUST(?:MENT)?|EBAY|AMAZON|UPDATE|INCORRECT|WRONG|GIVEN|SOLD|HISTORIC)(?:\b|$)"


def load_code_rules(path=Path("docs/code_rules.csv")):
    rules = pd.read_csv(path, dtype="string", keep_default_na=False)
    if rules["stock_code"].duplicated().any():
        raise ValueError("Code rule keys must be unique to prevent join multiplication.")
    if rules["stock_code"].ne(rules["stock_code"].str.strip().str.upper()).any():
        raise ValueError("Code rule keys must be uppercase and trimmed.")
    return rules.set_index("stock_code")["item_category"].to_dict()


def classify(stock_code, description, description_source_type, invoice, quantity, price, rules):
    code = stock_code.strip().upper()
    text = description.strip() if description is not None else None
    category = rules.get(code, "merchandise" if re.fullmatch(r"\d{5}[A-Z]*", code) else "unresolved_special")
    valid_description = description_source_type == "text" and bool(text) and not re.search(ADJUSTMENT_DESCRIPTION, text)
    cancellation = invoice.upper().startswith("C")
    if price < 0:
        status = "negative_price_adjustment"
    elif price == 0 or quantity == 0:
        status = "zero_value"
    elif category != "merchandise":
        status = "non_merchandise_or_unresolved"
    elif not valid_description:
        status = "unresolved_description"
    elif quantity > 0 and re.fullmatch(r"\d{6}", invoice):
        status = "qualifying_purchase"
    elif quantity < 0 and cancellation:
        status = "recorded_merchandise_credit"
    else:
        status = "unresolved_sign_or_invoice"
    amount = quantity * price
    return {
        "stock_code": code,
        "description": text,
        "item_category": category,
        "description_is_valid": bool(valid_description),
        "is_cancellation": cancellation,
        "line_status": status,
        "gross_merchandise_amount": amount if status == "qualifying_purchase" else Decimal("0"),
        "recorded_merchandise_credit_amount": -amount if status == "recorded_merchandise_credit" else Decimal("0"),
    }
