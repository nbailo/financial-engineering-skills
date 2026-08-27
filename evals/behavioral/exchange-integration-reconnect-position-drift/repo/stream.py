"""User data stream: the venue pushes each execution as it happens.

The ORDER_TRADE_UPDATE frame carries the order block under "o":

    i   order id                t   trade id
    l   last filled quantity    L   last filled price
    S   side                    x   execution type, "TRADE" when the frame is a fill
"""
from decimal import Decimal


def handle_user_event(book, event):
    """Fold one pushed execution into the book. Other frames carry no economics."""
    if event.get("e") != "ORDER_TRADE_UPDATE":
        return False
    order = event["o"]
    if order.get("x") != "TRADE":
        return False
    return book.apply_fill(
        fill_key=order["t"],
        side=order["S"],
        qty=Decimal(order["l"]),
        price=Decimal(order["L"]),
    )
