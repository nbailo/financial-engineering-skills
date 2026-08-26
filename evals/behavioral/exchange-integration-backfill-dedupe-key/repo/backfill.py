"""Recovery after a gap in the user data stream.

While the connection is down the venue keeps executing. The desk pulls the window it may have
missed from GET /fapi/v1/userTrades, whose rows look like:

    id      trade id            orderId   order id       symbol
    qty     filled quantity     price     fill price     side
"""
from decimal import Decimal


def fill_key(row):
    """Namespace the trade id by symbol, so ids from two symbols cannot collide."""
    return "%s:%s" % (row["symbol"], row["id"])


def replay_missed_trades(book, rows):
    """Fold the rows covering the gap into the book, newest window last.

    The endpoint returns a time window, not exactly the gap, so rows the stream already
    delivered come back with it. The book drops a key it has already recorded, so an
    overlapping window costs nothing.

    Rows are folded in the venue's own trade-id order, which is the economic order the
    average cost depends on, rather than in whatever order the pages arrived.
    """
    applied = 0
    for row in sorted(rows, key=lambda r: r["id"]):
        moved = book.apply_fill(
            fill_key=fill_key(row),
            side=row["side"],
            qty=Decimal(row["qty"]),
            price=Decimal(row["price"]),
        )
        if moved:
            applied += 1
    return applied
