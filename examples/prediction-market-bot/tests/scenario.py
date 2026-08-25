"""The two shared scenarios. Both are built from the same frozen operator script."""
from fake_venue import FakeVenue, load_market, load_script, run_script

SPLIT_RESOLVE = {"action": "resolve", "payout_numerators": [1, 1], "payout_denominator": 2}


def winner_takes_all():
    """The frozen scenario: buy 100 YES at 0.40, sell 30 at 0.70, YES resolves true."""
    venue = FakeVenue(load_market())
    return venue, run_script(venue, load_script())


def split():
    """The same trading, resolved half and half instead of to one side."""
    venue = FakeVenue(load_market())
    script = [step for step in load_script() if step["action"] != "resolve"]
    script.append(dict(SPLIT_RESOLVE))
    return venue, run_script(venue, script)


class StubAuthority:
    """A venue that answers one question, so a test can plant a disagreement."""

    def __init__(self, market_id: str, held: dict) -> None:
        self.market_id = market_id
        self.held = dict(held)

    def positions(self, market_id: str) -> dict:
        assert market_id == self.market_id
        return dict(self.held)
