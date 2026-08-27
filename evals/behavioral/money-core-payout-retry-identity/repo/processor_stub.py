"""In-process stand-in for the payment processor. No network.

It behaves the way the vendor documents the real one:

  * a charge is stored under the Idempotency-Key it arrives with;
  * a repeat of that key returns the charge the key already created and moves
    no further money, for 24 hours;
  * a request that times out may still have been recorded, so the caller
    cannot read a timeout as "nothing happened".
"""


class ProcessorTimeout(Exception):
    """The response never arrived. The charge may or may not exist."""


class Processor:
    def __init__(self, timeout_on_attempts=()):
        self.charges = {}
        self.attempts = 0
        self._timeout_on = set(timeout_on_attempts)

    def charge(self, key, account, amount_minor):
        self.attempts += 1
        existing = self.charges.get(key)
        if existing is None:
            existing = {"charge_id": "ch-%04d" % (len(self.charges) + 1),
                        "account": account, "amount_minor": amount_minor}
            self.charges[key] = existing
        if self.attempts in self._timeout_on:
            raise ProcessorTimeout(key)
        return existing

    def total_charged(self, account):
        return sum(c["amount_minor"] for c in self.charges.values()
                   if c["account"] == account)
