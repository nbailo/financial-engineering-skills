"""Bank withdrawals.

A requested withdrawal reserves the money when the customer asks for it. The payment
file goes out overnight and the bank acknowledges it the next business day.
"""

HOLD_DAYS = 7


def request_withdrawal(ledger, account, amount, currency, hold_id):
    if amount <= 0:
        raise ValueError("a withdrawal moves a positive amount")
    if ledger.available(account, currency) < amount:
        return ("declined", "insufficient_available_balance")
    ledger.place_hold(hold_id, account, amount, currency, ledger.today + HOLD_DAYS)
    return ("pending", hold_id)


def settle_withdrawal(ledger, hold_id, bank_account):
    """The bank acknowledged the payment file: the reservation becomes a posting."""
    ledger.capture_hold(hold_id, bank_account)


def cancel_withdrawal(ledger, hold_id):
    """The customer pulled the request before the file went out."""
    ledger.void_hold(hold_id)
