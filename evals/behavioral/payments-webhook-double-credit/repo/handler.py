"""Webhook handler. A settled refund becomes store credit on the customer's account.

The route verifies the provider signature and parses the body before calling handle(); that
lives in the web layer and not in here.
"""

from events import is_refund_event, refund_from_event


class RefundCreditHandler:
    def __init__(self, event_log, credit_book):
        self.event_log = event_log
        self.credit_book = credit_book

    def handle(self, event):
        if not self.event_log.claim(event["id"]):
            return "duplicate"
        if not is_refund_event(event):
            return "ignored"
        refund = refund_from_event(event)
        if refund["status"] != "succeeded":
            return "not_settled"
        self.credit_book.credit(
            refund["customer"], refund["amount"], "refund " + refund["id"]
        )
        return "credited"
