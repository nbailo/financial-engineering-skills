"""Hidden oracle: one settled refund is one store credit, however it is reported.

Every assertion is on final economic state as the store-credit service sees it: the money that
reached the service and the number of credits it had to write. The service the handler is given
counts those credits itself, at the point where a credit enters the service, so what is asserted
is the customer's balance at the service and the number of external writes that produced it.
Nothing here inspects which functions exist, and nothing depends on timing or on the order the
threads happened to run in.
"""
import os
import sys
import threading

sys.dont_write_bytecode = True

_CASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _candidate in (os.path.join(_CASE, "repo"), os.path.abspath("repo")):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from credit_book import CreditBook
from credit_service import StoreCreditService
from event_log import EventLog
from handler import RefundCreditHandler


class WatchedService(StoreCreditService):
    """The store-credit service, keeping its own count of the credits that reached it.

    A credit exists economically once it has entered the service; that is the moment counted
    here, and the balance asserted on is built from those credits alone. The round trip a real
    credit spends inside the service is where a second delivery of the same refund overlaps the
    first, so that is also where the rendezvous sits.
    """

    def __init__(self):
        StoreCreditService.__init__(self)
        self.__gate = None
        self.__ledger = []

    def _arm(self, gate):
        self.__gate = gate

    def apply_credit(self, customer_id, amount_minor, memo):
        gate = self.__gate
        if gate is not None:
            gate()
        result = StoreCreditService.apply_credit(self, customer_id, amount_minor, memo)
        self.__ledger.append((customer_id, amount_minor, memo))
        return result

    def credits(self):
        """Every credit that reached the service, in the order it arrived."""
        return list(self.__ledger)

    def credit_count(self):
        return len(self.__ledger)

    def credited(self, customer_id):
        """The customer's balance at the service, from the credits it actually wrote."""
        return sum(amount for cust, amount, _ in self.__ledger if cust == customer_id)

    def amounts(self):
        return sorted(amount for _, amount, _ in self.__ledger)


def refund(refund_id="re_501", amount=4500, status="succeeded", customer="cus_9",
           charge="ch_501"):
    return {
        "id": refund_id,
        "charge": charge,
        "customer": customer,
        "amount": amount,
        "status": status,
    }


def charge_refunded(event_id, *refunds):
    """The charge event carries the charge with every refund booked against it so far."""
    charge = {"id": refunds[-1]["charge"], "amount": 9900, "refunds": list(refunds)}
    return {"id": event_id, "type": "charge.refunded", "data": {"object": charge}}


def refund_updated(event_id, ref):
    return {"id": event_id, "type": "refund.updated", "data": {"object": ref}}


def build():
    service = WatchedService()
    handler = RefundCreditHandler(EventLog(), CreditBook(service))
    return service, handler


class Failure(Exception):
    pass


def want(label, actual, expected):
    if actual != expected:
        raise Failure("%s: expected %r, got %r" % (label, expected, actual))


def deliver_concurrently(service, handler, events):
    """Hand two separately generated deliveries to the handler at the same moment.

    The rendezvous sits inside the credit as the service receives it, i.e. after any claim the
    handler makes and before the money is written. Two callers that both get that far meet each
    other there. A caller the repair serialises never arrives, the barrier times out harmlessly,
    and that caller then sees whatever the first one reserved.
    """
    barrier = threading.Barrier(2, timeout=0.25)

    def rendezvous():
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass

    service._arm(rendezvous)
    errors = []

    def deliver(event):
        try:
            handler.handle(event)
        except Exception as exc:  # a repair that throws is not a repair
            errors.append("%s: %s" % (type(exc).__name__, exc))

    threads = [threading.Thread(target=deliver, args=(e,)) for e in events]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)
    for thread in threads:
        if thread.is_alive():
            raise Failure("a delivery never finished; the handler is stuck")
    if errors:
        raise Failure("a delivery raised: " + "; ".join(sorted(errors)))
    service._arm(None)


def concurrent_pair(tag, amount, customer="cus_9", refund_id="re_501"):
    """Both provider events for one refund, delivered at once, returning the final balance."""
    service, handler = build()
    ref = refund(refund_id=refund_id, amount=amount, customer=customer)
    deliver_concurrently(
        service,
        handler,
        [charge_refunded("evt_%s_a" % tag, ref), refund_updated("evt_%s_b" % tag, ref)],
    )
    return service, ref


# --- properties ---------------------------------------------------------------------------


def concurrent_same_refund_credits_once():
    """Three runs, same scenario: two events, one refund of 4500, one credit of 4500."""
    for attempt in range(1, 4):
        service, ref = concurrent_pair("c%d" % attempt, 4500)
        want("run %d credits reaching the service" % attempt, service.credit_count(), 1)
        want("run %d balance of %s" % (attempt, ref["customer"]),
             service.credited("cus_9"), 4500)


def concurrent_credit_is_not_a_special_case_of_4500():
    """Same collision at other amounts and other customers: still exactly one credit."""
    for tag, amount, customer, refund_id in (
        ("m1", 1299, "cus_31", "re_777"),
        ("m2", 7800, "cus_44", "re_778"),
        ("m3", 1, "cus_55", "re_779"),
    ):
        service, _ = concurrent_pair(tag, amount, customer=customer, refund_id=refund_id)
        want("credits for %s at %d" % (customer, amount), service.credit_count(), 1)
        want("balance of %s" % customer, service.credited(customer), amount)


def two_events_for_one_refund_credit_once_in_either_order():
    ref = refund()
    service, handler = build()
    handler.handle(charge_refunded("evt_a", ref))
    handler.handle(refund_updated("evt_b", ref))
    want("charge-first balance", service.credited("cus_9"), 4500)
    want("charge-first credits", service.credit_count(), 1)

    service, handler = build()
    handler.handle(refund_updated("evt_b", ref))
    handler.handle(charge_refunded("evt_a", ref))
    want("refund-first balance", service.credited("cus_9"), 4500)
    want("refund-first credits", service.credit_count(), 1)


def a_redelivery_of_one_event_id_credits_nothing_extra():
    ref = refund()
    service, handler = build()
    handler.handle(refund_updated("evt_b", ref))
    handler.handle(refund_updated("evt_b", ref))
    handler.handle(refund_updated("evt_b", ref))
    want("balance after three deliveries of evt_b", service.credited("cus_9"), 4500)
    want("credits after three deliveries of evt_b", service.credit_count(), 1)


def a_refund_credits_only_once_it_settles():
    service, handler = build()
    handler.handle(refund_updated("evt_p", refund(status="pending")))
    want("balance while pending", service.credited("cus_9"), 0)
    handler.handle(refund_updated("evt_s", refund()))
    want("balance once settled", service.credited("cus_9"), 4500)
    want("credits once settled", service.credit_count(), 1)


def a_second_genuine_refund_is_credited_in_full():
    first = refund()
    second = refund(refund_id="re_502", amount=1200, charge="ch_777")
    service, handler = build()
    handler.handle(charge_refunded("evt_a", first))
    handler.handle(refund_updated("evt_b", first))
    handler.handle(charge_refunded("evt_c", second))
    handler.handle(refund_updated("evt_d", second))
    want("balance after two refunds", service.credited("cus_9"), 5700)
    want("credits after two refunds", service.credit_count(), 2)


def two_partial_refunds_of_one_payment_are_both_credited():
    """Two partials on the same charge, each reported by both event types."""
    first = refund(refund_id="re_601", amount=2000)
    second = refund(refund_id="re_602", amount=2500)
    service, handler = build()
    handler.handle(charge_refunded("evt_1", first))
    handler.handle(refund_updated("evt_2", first))
    want("balance after the first partial", service.credited("cus_9"), 2000)
    handler.handle(charge_refunded("evt_3", first, second))
    handler.handle(refund_updated("evt_4", second))
    want("balance after both partials", service.credited("cus_9"), 4500)
    want("credits after both partials", service.credit_count(), 2)
    want("the amounts credited", service.amounts(), [2000, 2500])


def two_equal_partial_refunds_of_one_payment_are_both_credited():
    """Two returns of 1500 against one order are two refunds that are both owed."""
    first = refund(refund_id="re_621", amount=1500)
    second = refund(refund_id="re_622", amount=1500)
    service, handler = build()
    handler.handle(charge_refunded("evt_e1", first))
    handler.handle(refund_updated("evt_e2", first))
    want("balance after the first 1500", service.credited("cus_9"), 1500)
    handler.handle(charge_refunded("evt_e3", first, second))
    handler.handle(refund_updated("evt_e4", second))
    want("balance after both 1500s", service.credited("cus_9"), 3000)
    want("credits after both 1500s", service.credit_count(), 2)
    want("the amounts credited", service.amounts(), [1500, 1500])


def interleaved_deliveries_for_several_refunds_are_each_credited_once():
    """Workers hand over several refunds at once, so the deliveries arrive shuffled together.

    Every charge event lands before any of the refund events, so the news of a refund can be
    arbitrarily far from the news of that same refund.
    """
    first = refund(refund_id="re_651", amount=2100, charge="ch_651")
    second = refund(refund_id="re_652", amount=900, charge="ch_652")
    third = refund(refund_id="re_653", amount=4400, charge="ch_653")
    service, handler = build()
    handler.handle(charge_refunded("evt_i1", first))
    handler.handle(charge_refunded("evt_i2", second))
    handler.handle(charge_refunded("evt_i3", third))
    handler.handle(refund_updated("evt_i4", first))
    handler.handle(refund_updated("evt_i5", second))
    handler.handle(refund_updated("evt_i6", third))
    want("balance after the interleaved deliveries", service.credited("cus_9"), 7400)
    want("credits after the interleaved deliveries", service.credit_count(), 3)
    want("the amounts credited", service.amounts(), [900, 2100, 4400])


def two_partial_refunds_colliding_are_both_credited_once():
    """Each partial arrives as a colliding pair; two refunds, two credits, no more."""
    service, handler = build()
    first = refund(refund_id="re_611", amount=3000)
    second = refund(refund_id="re_612", amount=1500)
    deliver_concurrently(
        service, handler,
        [charge_refunded("evt_p1a", first), refund_updated("evt_p1b", first)],
    )
    deliver_concurrently(
        service, handler,
        [charge_refunded("evt_p2a", first, second), refund_updated("evt_p2b", second)],
    )
    want("balance after two colliding partials", service.credited("cus_9"), 4500)
    want("credits after two colliding partials", service.credit_count(), 2)


def two_distinct_refunds_colliding_are_both_credited():
    """Two different refunds in flight together are two debts; neither may be swallowed."""
    service, handler = build()
    first = refund(refund_id="re_661", amount=2400, charge="ch_661")
    second = refund(refund_id="re_662", amount=600, charge="ch_662")
    deliver_concurrently(
        service, handler,
        [charge_refunded("evt_d1", first), refund_updated("evt_d2", second)],
    )
    want("balance after two colliding refunds", service.credited("cus_9"), 3000)
    want("credits after two colliding refunds", service.credit_count(), 2)
    want("the amounts credited", service.amounts(), [600, 2400])


def either_event_type_alone_credits_the_refund_in_full():
    """Not every refund is reported by both events; whichever one arrives has to credit it."""
    service, handler = build()
    handler.handle(charge_refunded("evt_only_charge", refund(refund_id="re_801", amount=3300)))
    want("balance from charge.refunded alone", service.credited("cus_9"), 3300)
    want("credits from charge.refunded alone", service.credit_count(), 1)

    service, handler = build()
    handler.handle(refund_updated("evt_only_refund", refund(refund_id="re_802", amount=3300)))
    want("balance from refund.updated alone", service.credited("cus_9"), 3300)
    want("credits from refund.updated alone", service.credit_count(), 1)


def two_distinct_refunds_of_the_same_amount_are_both_credited():
    """Two returns of the same price are two refunds, not one reported twice."""
    first = refund(refund_id="re_701", amount=2500, charge="ch_701")
    second = refund(refund_id="re_702", amount=2500, charge="ch_702")
    service, handler = build()
    handler.handle(charge_refunded("evt_s1", first))
    handler.handle(refund_updated("evt_s2", first))
    handler.handle(charge_refunded("evt_s3", second))
    handler.handle(refund_updated("evt_s4", second))
    want("balance after two refunds of 2500", service.credited("cus_9"), 5000)
    want("credits after two refunds of 2500", service.credit_count(), 2)


def an_unrelated_event_credits_nothing():
    service, handler = build()
    handler.handle(
        {"id": "evt_x", "type": "charge.succeeded", "data": {"object": {"id": "ch_501"}}}
    )
    want("balance after an unrelated event", service.credited("cus_9"), 0)
    want("credits after an unrelated event", service.credit_count(), 0)


PROPERTIES = (
    ("two events for one refund, delivered at once, credit it once (3 runs)",
     concurrent_same_refund_credits_once),
    ("the same collision at other amounts and customers credits once",
     concurrent_credit_is_not_a_special_case_of_4500),
    ("two events for one refund credit it once in either arrival order",
     two_events_for_one_refund_credit_once_in_either_order),
    ("a redelivery of the same provider event id credits nothing extra",
     a_redelivery_of_one_event_id_credits_nothing_extra),
    ("a refund is credited only once it settles",
     a_refund_credits_only_once_it_settles),
    ("a second genuine refund on the same customer is credited in full",
     a_second_genuine_refund_is_credited_in_full),
    ("two partial refunds of one payment are both credited",
     two_partial_refunds_of_one_payment_are_both_credited),
    ("two partial refunds of one payment for the same amount are both credited",
     two_equal_partial_refunds_of_one_payment_are_both_credited),
    ("deliveries for several refunds arriving interleaved credit each once",
     interleaved_deliveries_for_several_refunds_are_each_credited_once),
    ("two colliding partial refunds of one payment are each credited once",
     two_partial_refunds_colliding_are_both_credited_once),
    ("two different refunds colliding are both credited",
     two_distinct_refunds_colliding_are_both_credited),
    ("either provider event type on its own credits the refund in full",
     either_event_type_alone_credits_the_refund_in_full),
    ("two distinct refunds of the same amount are both credited",
     two_distinct_refunds_of_the_same_amount_are_both_credited),
    ("an unrelated event credits nothing",
     an_unrelated_event_credits_nothing),
)


def main():
    failures = []
    for label, check in PROPERTIES:
        try:
            check()
        except Failure as failure:
            failures.append((label, str(failure)))
            print("FAIL  %s" % label)
            print("      %s" % failure)
        except Exception as exc:
            failures.append((label, "%s: %s" % (type(exc).__name__, exc)))
            print("FAIL  %s" % label)
            print("      %s: %s" % (type(exc).__name__, exc))
        else:
            print("ok    %s" % label)
    if failures:
        print("%d of %d properties failed" % (len(failures), len(PROPERTIES)))
        return 1
    print("all %d properties hold" % len(PROPERTIES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
