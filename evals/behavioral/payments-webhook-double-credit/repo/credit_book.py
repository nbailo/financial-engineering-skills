"""Store credit handed back to a customer when a refund settles.

The handler talks to this and never to the service client directly, so anything that has to be
true of every credit belongs in here.
"""


class CreditBook:
    def __init__(self, service):
        self.service = service

    def credit(self, customer_id, amount_minor, memo):
        if amount_minor <= 0:
            raise ValueError("a credit must be positive")
        return self.service.apply_credit(customer_id, amount_minor, memo)

    def balance(self, customer_id):
        return self.service.balance(customer_id)
