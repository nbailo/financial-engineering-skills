#!/usr/bin/env python3
"""Run the safe and the unsafe path over the same fixtures and print both ledgers.

    python3 examples/payment-ledger-reconciliation/demo.py

Each path meets the same three events: an ambiguous timeout on the charge, an injected
failure between
the charge and the local write, and one settlement event delivered twice. The safe path runs
two workers over one shared store; the unsafe path keeps its state on the instance.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests import netguard  # noqa: E402

netguard.install()                      # before anything else, so no accident can dial out

from fake_processor import (FakeProcessor, load_settlement_report,  # noqa: E402
                            load_webhooks)
from ledger import SUSPENSE, Ledger, exposure  # noqa: E402
from safe_flow import SafeFlow, InjectedFailure, intent_key  # noqa: E402
from store import Store  # noqa: E402
from unsafe_flow import UnsafeFlow  # noqa: E402

INVOICE, AMOUNT_MINOR, CURRENCY, BATCH = "INV-1001", 12_500, "USD", "SB-0001"


def major(minor_units: int) -> str:
    sign, n = ("-" if minor_units < 0 else ""), abs(minor_units)
    return f"{sign}{n // 100}.{n % 100:02d}"


def per_currency(amounts: dict) -> str:
    """Every figure names its currency and no two of them are added together."""
    return "; ".join(f"{c} {major(v)}" for c, v in amounts.items()) or "-"


def trial(ledger) -> str:
    return per_currency(ledger.trial_balance())


def fail_once():
    """Fires once, after the external effect and before the local write."""
    armed = [True]

    def hook():
        if armed and armed.pop():
            raise InjectedFailure("power lost after the charge, before the ledger write")

    return hook


def run_safe():
    processor = FakeProcessor(ambiguous_keys={intent_key(INVOICE)})
    store = Store(Ledger())
    try:
        SafeFlow(store, processor).pay_invoice(INVOICE, AMOUNT_MINOR, CURRENCY,
                                               fail_after_send=fail_once())
    except InjectedFailure:
        pass
    reboot = SafeFlow(store, processor)     # a second worker over the same shared store
    reboot.recover()
    processor.settle(store.get_intent(intent_key(INVOICE)).charge_id, BATCH)
    for envelope in load_webhooks():
        reboot.handle_webhook(envelope)
    return processor, store, reboot.run_reconciliation(load_settlement_report())


def run_unsafe():
    processor = FakeProcessor(ambiguous_keys={f"{INVOICE}-attempt1"})
    ledger = Ledger()
    flow = UnsafeFlow(ledger)
    try:
        flow.pay_invoice(processor, INVOICE, AMOUNT_MINOR, CURRENCY,
                         fail_after_send=fail_once())
    except InjectedFailure:
        pass
    flow.pay_invoice(processor, INVOICE, AMOUNT_MINOR, CURRENCY)   # the operator re-runs it
    for envelope in load_webhooks():
        flow.handle_webhook(envelope)
    return processor, ledger, flow.run_reconciliation(load_settlement_report())


def main() -> int:
    safe_p, safe_store, safe_breaks = run_safe()
    bad_p, bad_l, bad_breaks = run_unsafe()
    safe_l = safe_store.ledger
    safe_b, bad_b = safe_l.balances(), bad_l.balances()
    rows = [(f"{account} [{currency}]", major(safe_b.get(k, 0)), major(bad_b.get(k, 0)))
            for k in sorted(set(safe_b) | set(bad_b)) for account, currency in (k,)]
    rows += [("-- trial balance", trial(safe_l), trial(bad_l)),
             ("-- charges at the processor", safe_p.charge_count(INVOICE),
              bad_p.charge_count(INVOICE)),
             ("-- the customer was charged", major(safe_p.charged_total_minor(INVOICE)),
              major(bad_p.charged_total_minor(INVOICE))),
             ("-- open reconciliation breaks", len(safe_breaks), len(bad_breaks)),
             ("-- reported at stake, per currency", per_currency(exposure(safe_breaks)),
              per_currency(exposure(bad_breaks)))]
    print(f"One invoice, {INVOICE}, for {major(AMOUNT_MINOR)} {CURRENCY}, against a fake "
          f"processor in this\nprocess. No external or live processor, no network, no "
          f"credentials.\n\n  {'':<34}{'safe':>16}{'unsafe':>16}")
    for label, s, u in rows:
        print(f"  {label:<34}{s:>16}{u:>16}")
    print()
    for brk in safe_breaks:
        print(f"  safe break, open and unposted:\n    {brk.describe()}")
    for brk in bad_breaks:
        print(f"  unsafe break, after the plug:\n    {brk.describe()}")
    print(f"\nThe unsafe path charged {major(bad_p.charged_total_minor(INVOICE))} for a "
          f"{major(AMOUNT_MINOR)} invoice, credited the settlement\ntwice, then forced cash "
          f"and fees to the report and dropped "
          f"{major(bad_b.get((SUSPENSE, CURRENCY), 0))}\ninto {SUSPENSE}. Nothing raised, "
          f"and the local intent and ledger do not\nattribute the two extra charges to anything.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
