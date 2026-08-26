"""The 250 ms hedge cycle.

Each tick carries the residual delta of the options book, this cycle's mark, the venue's
own position for the symbol, and the intent id the scheduler committed before the tick.
"""
from hedger import send_hedge_order


def run_cycles(client, ticks):
    """Run one hedge cycle per tick and return the orders that reached the venue."""
    sent = []
    for tick in ticks:
        order = send_hedge_order(
            client,
            tick["symbol"],
            tick["residual_delta"],
            tick["mark_price"],
            tick["mark_age_ms"],
            tick["position"],
            tick["intent_id"],
        )
        if order is not None:
            sent.append(order)
    return sent
