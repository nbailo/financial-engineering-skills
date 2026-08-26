"""Delta hedger for the options book: turn a residual delta into a perp order.

The scheduler recomputes the book delta every 250 ms and hands the residual to
send_hedge_order, which is the only path to the venue. Every refusal that protects the
desk lives inside it: the venue's own filters, the staleness gate on the mark, and the
desk's own position and notional caps.
"""
from decimal import Decimal, ROUND_DOWN

from filters import SYMBOL_FILTERS

MAX_MARK_AGE_MS = 2000
MAX_ABS_POSITION = Decimal("2")
MAX_ORDER_NOTIONAL = Decimal("250000")


def floor_to(value, increment):
    """The largest multiple of increment that is not above value."""
    steps = (value / increment).to_integral_value(rounding=ROUND_DOWN)
    return steps * increment


def send_hedge_order(client, symbol, residual_delta, mark_price, mark_age_ms,
                     position, intent_id):
    """Hedge one cycle's residual delta and return the order sent, or None.

    residual_delta is signed and in base units: positive means the options book is long
    and the hedge sells. mark_price and mark_age_ms come from this cycle's read of the
    venue's mark stream. position is the venue's own position for the symbol, read this
    cycle. intent_id is the monotonic hedge-intent id the scheduler commits before the
    cycle runs, so a retry of this cycle reuses it.
    """
    venue = SYMBOL_FILTERS[symbol]
    if mark_age_ms > MAX_MARK_AGE_MS:
        return None

    side = "SELL" if residual_delta > 0 else "BUY"
    qty = floor_to(abs(residual_delta), venue["stepSize"])
    if qty < venue["minQty"]:
        qty = venue["minQty"]

    if qty * mark_price < venue["notional"]:
        return None
    if qty * mark_price > MAX_ORDER_NOTIONAL:
        return None
    signed = qty if side == "BUY" else -qty
    if abs(position + signed) > MAX_ABS_POSITION:
        return None

    order = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": qty,
        "newClientOrderId": "hedge-%s-%d" % (symbol.lower(), intent_id),
    }
    client.new_order(order)
    return order
