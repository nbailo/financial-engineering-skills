"""The provider events this endpoint is subscribed to.

charge.refunded carries the charge with its refunds attached. refund.updated carries the refund
on its own and is what tells us a partial refund reached its final status, which the charge event
alone did not cover. The provider generates each of them as its own event object with its own id.
"""

REFUND_EVENT_TYPES = ("charge.refunded", "refund.updated")


def is_refund_event(event):
    return event["type"] in REFUND_EVENT_TYPES


def refund_from_event(event):
    """The refund object this event is about, whichever event type carried it."""
    obj = event["data"]["object"]
    if event["type"] == "charge.refunded":
        return obj["refunds"][-1]
    return obj
