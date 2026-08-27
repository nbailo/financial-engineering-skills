"""Venue trading rules for the perpetual symbols this desk hedges on.

Mirrors the fields the venue publishes under GET /fapi/v1/exchangeInfo: stepSize and
minQty come from the LOT_SIZE filter, tickSize from PRICE_FILTER, notional from
MIN_NOTIONAL. Every amount is a decimal string exactly as the venue serialises it, so
nothing here is ever a float.
"""
from decimal import Decimal

SYMBOL_FILTERS = {
    "BTCUSDT": {
        "stepSize": Decimal("0.001"),
        "minQty": Decimal("0.002"),
        "tickSize": Decimal("0.10"),
        "notional": Decimal("5"),
    },
    "ETHUSDT": {
        "stepSize": Decimal("0.01"),
        "minQty": Decimal("0.02"),
        "tickSize": Decimal("0.01"),
        "notional": Decimal("5"),
    },
}
