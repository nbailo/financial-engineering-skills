"""The processor settlement report: the authority for what left the account.

One line per transfer, `transfer_id,vendor,amount`, all in the account currency.
Amounts are written out in full, so they parse exactly.
"""

from decimal import Decimal


def parse_report(text):
    rows = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        transfer_id, vendor, amount = [field.strip() for field in line.split(",")]
        rows.append({
            "transfer_id": transfer_id,
            "vendor": vendor,
            "amount": Decimal(amount),
        })
    return rows
