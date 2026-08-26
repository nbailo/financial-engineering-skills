"""Store credit handed back to a customer when a refund settles."""


class CreditBook:
    def __init__(self):
        self.balances = {}
        self.journal = []

    def credit(self, customer_id, amount_minor, memo):
        if amount_minor <= 0:
            raise ValueError("a credit must be positive")
        self.balances[customer_id] = self.balances.get(customer_id, 0) + amount_minor
        self.journal.append({"customer_id": customer_id, "amount": amount_minor, "memo": memo})
        return self.balances[customer_id]

    def balance(self, customer_id):
        return self.balances.get(customer_id, 0)
