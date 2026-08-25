#!/usr/bin/env python3
"""Run both bots over the same frozen event log and print what they believe afterwards.

    python3 examples/prediction-market-bot/demo.py

Offline: the network guard is installed before anything else, so this cannot reach a venue
even by accident.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tests import netguard  # noqa: E402

netguard.install()

from fake_venue import FakeVenue, load_market, load_script, run_script  # noqa: E402
from safe_bot import rebuild  # noqa: E402
from unsafe_bot import UnsafeBot  # noqa: E402

MICRO = 1_000_000
MARKET_ID = "FAKE-BINARY-1"


def amount(micro: int) -> str:
    sign = "-" if micro < 0 else ""
    micro = abs(micro)
    return f"{sign}{micro // MICRO}.{micro % MICRO:06d}"


def session(resolve_step: dict):
    market = load_market()
    venue = FakeVenue(market)
    script = [s for s in load_script() if s["action"] != "resolve"] + [resolve_step]
    events = run_script(venue, script)
    return market, venue, events


def run(title: str, resolve_step: dict, reconnect: bool) -> None:
    market, venue, events = session(resolve_step)
    safe = rebuild(market, venue, events)
    unsafe = UnsafeBot(market=market)
    unsafe.apply_all(events)
    if reconnect:
        redelivered = venue.settlement_events()
        safe.apply_all(redelivered)
        unsafe.apply_all(redelivered)

    print(f"\n{title}")
    print("-" * len(title))
    rows = [
        ("FUSD available", amount(safe.available["FUSD"]), amount(unsafe.available["FUSD"])),
        ("FUSD held for resting orders", amount(safe.reserved["FUSD"]), "not modelled"),
        ("FPOINT available", amount(safe.available["FPOINT"]),
         amount(unsafe.available["FPOINT"])),
        ("fees paid", f"{amount(safe.fees_paid['FPOINT'])} FPOINT",
         f"{amount(unsafe.fee_paid)} FUSD"),
        ("payout credited", amount(safe.settlement_credit.get(MARKET_ID, 0)),
         amount(unsafe.settlement_credited)),
        ("YES position", str(safe.positions["YES"]), str(unsafe.positions["YES"])),
    ]
    print(f"  {'':<30}{'safe':>18}{'unsafe':>18}")
    for label, left, right in rows:
        print(f"  {label:<30}{left:>18}{right:>18}")

    gap = unsafe.available["FUSD"] - safe.available["FUSD"]
    print(f"  {'difference in FUSD':<30}{'':>18}{amount(gap):>18}")
    for alert in safe.alerts:
        print(f"  safe bot alert: {alert}")


def main() -> int:
    print("A fake venue, in this process. No network, no credentials, no live mode.")
    run("Scenario A: YES resolves true, then the settlement stream reconnects",
        {"action": "resolve", "payout_numerators": [1, 0], "payout_denominator": 1},
        reconnect=True)
    run("Scenario B: the market resolves half and half, no reconnect",
        {"action": "resolve", "payout_numerators": [1, 1], "payout_denominator": 2},
        reconnect=False)
    print("\nIn A the unsafe bot books the payout twice. In B it books nothing, because a")
    print("split resolution has no winning index to read. Neither raises.")
    print("\nRun the tests:  python3 examples/prediction-market-bot/run_tests.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
