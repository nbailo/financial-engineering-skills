"""Daily comparison of our payout rows against the processor settlement report.

Runs once the report for the book date has landed. Every difference becomes a break
record and goes to the alert sink; nothing is posted automatically, ops works the list.
"""

from decimal import Decimal

from settlement_report import parse_report

ZERO = Decimal("0.00")


def _break(kind, transfer_id, ours, theirs):
    return {
        "kind": kind,
        "transfer_id": transfer_id,
        "ours": ours,
        "theirs": theirs,
        "amount": abs(theirs - ours),
    }


def reconcile(local_rows, report_rows):
    """One break record per difference between our payouts and the report."""
    reported = {row["transfer_id"]: row for row in report_rows}
    breaks = []
    for row in local_rows:
        theirs = reported.get(row["transfer_id"])
        if theirs is None:
            breaks.append(_break("not_in_report", row["transfer_id"], row["amount"], ZERO))
            continue
        if theirs["amount"] != row["amount"]:
            breaks.append(_break("amount_differs", row["transfer_id"],
                                 row["amount"], theirs["amount"]))
    return breaks


def run_daily(store, report_text, book_date, alert):
    """Compare one book date and deliver every break to `alert`.

    `alert` has no default. A run with nowhere to send a break is a run that
    cannot report one, so the caller has to name the destination.
    """
    report_rows = parse_report(report_text)
    if not report_rows:
        return {"status": "clean", "breaks": []}
    breaks = reconcile(store.rows_for(book_date), report_rows)
    for record in breaks:
        alert(record)
    return {"status": "clean" if not breaks else "breaks", "breaks": breaks}
