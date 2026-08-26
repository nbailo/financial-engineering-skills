"""Delivery record for provider events.

The provider redelivers an event under the same event id until the endpoint answers 2xx, so
one row per event id is what keeps a redelivery from being handled a second time. claim() is
the check and the insert in one step.
"""


class EventLog:
    def __init__(self):
        self._delivered = set()

    def claim(self, event_id):
        """True the first time this delivery is seen, False for every redelivery of it."""
        if event_id in self._delivered:
            return False
        self._delivered.add(event_id)
        return True

    def delivered(self):
        return sorted(self._delivered)
