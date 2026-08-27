"""Client for the store-credit service that holds customer balances.

Applying a credit is a round trip to another service and is by far the slowest step in handling a
refund event. The webhook endpoint runs several delivery workers, so two deliveries reporting the
same refund are routinely inside that round trip at the same moment; whatever guards the credit
has to still hold while one worker is parked in here.

before_write is where the round trip is spent. Integration tests hang a stand-in on it so that
overlap is reproducible without a network.
"""


class StoreCreditService:
    def __init__(self):
        self._balances = {}
        self.writes = []
        self.before_write = None

    def apply_credit(self, customer_id, amount_minor, memo):
        """Move amount_minor of store credit onto the customer. One call, one write."""
        if self.before_write is not None:
            self.before_write()
        self._balances[customer_id] = self._balances.get(customer_id, 0) + amount_minor
        self.writes.append(
            {"customer_id": customer_id, "amount": amount_minor, "memo": memo}
        )
        return self._balances[customer_id]

    def balance(self, customer_id):
        return self._balances.get(customer_id, 0)
