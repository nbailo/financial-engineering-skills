"""Hidden oracle: one settled refund is one store credit, however it is reported.

Every assertion is on final economic state as the store-credit service sees it: the money that
reached the service, the credits it had to write, the balance those writes left behind, and the
round trips the service spent moving that money. A credit the customer can spend is one the
service actually carried out, so a credit that appears without its round trip has not been
handed over at all. The service the workers are given is the real one, and the credits counted
here are the writes it records for itself. Nothing here inspects which functions exist, and
nothing depends on timing or on the order the threads happened to run in.

The endpoint runs several delivery workers. A worker is a handler with its own delivery record and
its own credit book; what the workers have in common is the one store-credit service behind them,
so that service is the only place a claim on a refund can mean anything to all of them.
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
    """The store-credit service, read for the credits it wrote and the balances they left.

    A credit exists economically once the service has written it, which is what self.writes
    records, and the customer's balance is what those writes add up to. Handing the money over
    costs the service one round trip per credit, counted here as round_trips once it has come
    back, so a credit that turns up without one was never carried out. The round trip is also
    where a second delivery of the same refund overlaps the first, so the rendezvous is hung
    inside it.
    """

    def __init__(self):
        StoreCreditService.__init__(self)
        self.round_trips = 0
        self._gate = None
        self._counter_lock = threading.Lock()
        self.before_write = self._round_trip

    def _round_trip(self):
        """One round trip to the store-credit service, handing over one credit.

        Counted once it has come back, so a hand-over the service refused is not counted as
        money handed over.
        """
        gate = self._gate
        if gate is not None:
            gate()
        with self._counter_lock:
            self.round_trips += 1

    def _arm(self, gate):
        self._gate = gate

    def credits(self):
        """Every credit the service wrote, in the order it wrote it."""
        return [(w["customer_id"], w["amount"], w["memo"]) for w in self.writes]

    def credit_count(self):
        return len(self.writes)

    def credited(self, customer_id):
        """The customer's store credit, from the writes the service actually made."""
        return sum(w["amount"] for w in self.writes if w["customer_id"] == customer_id)

    def amounts(self):
        return sorted(w["amount"] for w in self.writes)


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


def worker(service):
    """One delivery worker: its own delivery record, its own credit book, the shared service."""
    return RefundCreditHandler(EventLog(), CreditBook(service))


def build():
    service = WatchedService()
    return service, worker(service)


def build_workers(count=2):
    """Several delivery workers of the same endpoint, sharing one store-credit service."""
    service = WatchedService()
    return service, [worker(service) for _ in range(count)]


class Failure(Exception):
    pass


def want(label, actual, expected):
    if actual != expected:
        raise Failure("%s: expected %r, got %r" % (label, expected, actual))


def want_state(label, service, customer, credits, balance):
    """The whole economic picture: credits written, money credited, balance left behind.

    Every credit the customer ends up with cost the service exactly one round trip to hand it
    over, so the round trips are part of that picture: money that appears on a balance without
    one was never handed over, and a round trip that leaves no credit spent money for nothing.
    """
    want("%s: credits the service wrote" % label, service.credit_count(), credits)
    want("%s: credited to %s" % (label, customer), service.credited(customer), balance)
    want("%s: balance of %s" % (label, customer), service.balance(customer), balance)
    want("%s: round trips the service spent" % label, service.round_trips, credits)


def deliver_concurrently(service, deliveries):
    """Hand separately generated deliveries to their workers at the same moment.

    deliveries is a list of (worker, event). The rendezvous sits inside the credit as the service
    receives it, i.e. after any claim a worker makes on its own and before the money is written.
    Two callers that both get that far meet each other there. A caller the repair serialises never
    arrives, the barrier times out harmlessly, and that caller then sees whatever the first one
    reserved.
    """
    barrier = threading.Barrier(2, timeout=0.25)

    def rendezvous():
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass

    service._arm(rendezvous)
    errors = []

    def deliver(handler, event):
        try:
            handler.handle(event)
        except Exception as exc:  # a repair that throws is not a repair
            errors.append("%s: %s" % (type(exc).__name__, exc))

    threads = [threading.Thread(target=deliver, args=d) for d in deliveries]
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
    """Both provider events for one refund, delivered at once to one worker."""
    service, handler = build()
    ref = refund(refund_id=refund_id, amount=amount, customer=customer)
    deliver_concurrently(
        service,
        [(handler, charge_refunded("evt_%s_a" % tag, ref)),
         (handler, refund_updated("evt_%s_b" % tag, ref))],
    )
    return service, ref


def concurrent_pair_across_workers(tag, amount, customer="cus_9", refund_id="re_501"):
    """The same refund reported to two different workers of the endpoint at the same moment."""
    service, (first_worker, second_worker) = build_workers(2)
    ref = refund(refund_id=refund_id, amount=amount, customer=customer)
    deliver_concurrently(
        service,
        [(first_worker, charge_refunded("evt_%s_a" % tag, ref)),
         (second_worker, refund_updated("evt_%s_b" % tag, ref))],
    )
    return service, ref


# --- properties ---------------------------------------------------------------------------


def concurrent_same_refund_credits_once():
    """Three runs, same scenario: two events, one refund of 4500, one credit of 4500."""
    for attempt in range(1, 4):
        service, _ = concurrent_pair("c%d" % attempt, 4500)
        want_state("run %d" % attempt, service, "cus_9", 1, 4500)


def concurrent_credit_is_not_a_special_case_of_4500():
    """Same collision at other amounts and other customers: still exactly one credit."""
    for tag, amount, customer, refund_id in (
        ("m1", 1299, "cus_31", "re_777"),
        ("m2", 7800, "cus_44", "re_778"),
        ("m3", 1, "cus_55", "re_779"),
    ):
        service, _ = concurrent_pair(tag, amount, customer=customer, refund_id=refund_id)
        want_state("%s at %d" % (customer, amount), service, customer, 1, amount)


def two_workers_colliding_on_one_refund_credit_it_once():
    """The regression: two workers, one shared store-credit service, one refund.

    Each worker has its own delivery record and its own credit book, so neither can see that the
    other has taken the refund; the two provider events are separately generated, with different
    event ids, and arrive at the same moment. Three runs, and the customer is owed 4500 once.
    """
    for attempt in range(1, 4):
        service, _ = concurrent_pair_across_workers("w%d" % attempt, 4500)
        want_state("two workers colliding, run %d" % attempt, service, "cus_9", 1, 4500)


def two_workers_in_sequence_credit_one_refund_once():
    """The same two workers, one after the other rather than at once: still one credit."""
    ref = refund()
    service, (first_worker, second_worker) = build_workers(2)
    first_worker.handle(charge_refunded("evt_wa", ref))
    want_state("after the first worker", service, "cus_9", 1, 4500)
    second_worker.handle(refund_updated("evt_wb", ref))
    want_state("after the second worker", service, "cus_9", 1, 4500)

    service, (first_worker, second_worker) = build_workers(2)
    first_worker.handle(refund_updated("evt_wc", ref))
    second_worker.handle(charge_refunded("evt_wd", ref))
    want_state("after the workers in the other order", service, "cus_9", 1, 4500)


def two_events_for_one_refund_credit_once_in_either_order():
    ref = refund()
    service, handler = build()
    handler.handle(charge_refunded("evt_a", ref))
    handler.handle(refund_updated("evt_b", ref))
    want_state("charge first", service, "cus_9", 1, 4500)

    service, handler = build()
    handler.handle(refund_updated("evt_b", ref))
    handler.handle(charge_refunded("evt_a", ref))
    want_state("refund first", service, "cus_9", 1, 4500)


def a_redelivery_of_one_event_id_credits_nothing_extra():
    ref = refund()
    service, handler = build()
    handler.handle(refund_updated("evt_b", ref))
    handler.handle(refund_updated("evt_b", ref))
    handler.handle(refund_updated("evt_b", ref))
    want_state("after three deliveries of evt_b", service, "cus_9", 1, 4500)


def a_refund_credits_only_once_it_settles():
    service, handler = build()
    handler.handle(refund_updated("evt_p", refund(status="pending")))
    want_state("while pending", service, "cus_9", 0, 0)
    handler.handle(refund_updated("evt_s", refund()))
    want_state("once settled", service, "cus_9", 1, 4500)


def a_second_genuine_refund_is_credited_in_full():
    first = refund()
    second = refund(refund_id="re_502", amount=1200, charge="ch_777")
    service, handler = build()
    handler.handle(charge_refunded("evt_a", first))
    handler.handle(refund_updated("evt_b", first))
    handler.handle(charge_refunded("evt_c", second))
    handler.handle(refund_updated("evt_d", second))
    want_state("after two refunds", service, "cus_9", 2, 5700)


def a_second_genuine_refund_through_another_worker_is_credited_in_full():
    """The second refund happens to be handled by a different worker: it is still owed."""
    first = refund()
    second = refund(refund_id="re_512", amount=1200, charge="ch_778")
    service, (first_worker, second_worker) = build_workers(2)
    first_worker.handle(charge_refunded("evt_wa1", first))
    second_worker.handle(refund_updated("evt_wb1", first))
    second_worker.handle(charge_refunded("evt_wa2", second))
    first_worker.handle(refund_updated("evt_wb2", second))
    want_state("after two refunds across workers", service, "cus_9", 2, 5700)
    want("the amounts credited", service.amounts(), [1200, 4500])


def two_partial_refunds_of_one_payment_are_both_credited():
    """Two partials on the same charge, each reported by both event types."""
    first = refund(refund_id="re_601", amount=2000)
    second = refund(refund_id="re_602", amount=2500)
    service, handler = build()
    handler.handle(charge_refunded("evt_1", first))
    handler.handle(refund_updated("evt_2", first))
    want_state("after the first partial", service, "cus_9", 1, 2000)
    handler.handle(charge_refunded("evt_3", first, second))
    handler.handle(refund_updated("evt_4", second))
    want_state("after both partials", service, "cus_9", 2, 4500)
    want("the amounts credited", service.amounts(), [2000, 2500])


def two_partial_refunds_across_workers_are_both_credited():
    """The two partials of one payment reach different workers: both are still owed."""
    first = refund(refund_id="re_631", amount=2000)
    second = refund(refund_id="re_632", amount=2500)
    service, (first_worker, second_worker) = build_workers(2)
    first_worker.handle(charge_refunded("evt_x1", first))
    second_worker.handle(refund_updated("evt_x2", first))
    want_state("after the first partial across workers", service, "cus_9", 1, 2000)
    second_worker.handle(charge_refunded("evt_x3", first, second))
    first_worker.handle(refund_updated("evt_x4", second))
    want_state("after both partials across workers", service, "cus_9", 2, 4500)
    want("the amounts credited", service.amounts(), [2000, 2500])


def two_equal_partial_refunds_of_one_payment_are_both_credited():
    """Two returns of 1500 against one order are two refunds that are both owed."""
    first = refund(refund_id="re_621", amount=1500)
    second = refund(refund_id="re_622", amount=1500)
    service, (first_worker, second_worker) = build_workers(2)
    first_worker.handle(charge_refunded("evt_e1", first))
    second_worker.handle(refund_updated("evt_e2", first))
    want_state("after the first 1500", service, "cus_9", 1, 1500)
    second_worker.handle(charge_refunded("evt_e3", first, second))
    first_worker.handle(refund_updated("evt_e4", second))
    want_state("after both 1500s", service, "cus_9", 2, 3000)
    want("the amounts credited", service.amounts(), [1500, 1500])


def interleaved_deliveries_for_several_refunds_are_each_credited_once():
    """Workers hand over several refunds at once, so the deliveries arrive shuffled together.

    Every charge event lands before any of the refund events, and the two workers take them in
    turn, so the news of a refund can be arbitrarily far from the news of that same refund and
    can reach a different worker than the first delivery did.
    """
    first = refund(refund_id="re_651", amount=2100, charge="ch_651")
    second = refund(refund_id="re_652", amount=900, charge="ch_652")
    third = refund(refund_id="re_653", amount=4400, charge="ch_653")
    service, (first_worker, second_worker) = build_workers(2)
    first_worker.handle(charge_refunded("evt_i1", first))
    second_worker.handle(charge_refunded("evt_i2", second))
    first_worker.handle(charge_refunded("evt_i3", third))
    second_worker.handle(refund_updated("evt_i4", first))
    first_worker.handle(refund_updated("evt_i5", second))
    second_worker.handle(refund_updated("evt_i6", third))
    want_state("after the interleaved deliveries", service, "cus_9", 3, 7400)
    want("the amounts credited", service.amounts(), [900, 2100, 4400])


def two_partial_refunds_colliding_are_both_credited_once():
    """Each partial arrives as a colliding pair; two refunds, two credits, no more."""
    service, (first_worker, second_worker) = build_workers(2)
    first = refund(refund_id="re_611", amount=3000)
    second = refund(refund_id="re_612", amount=1500)
    deliver_concurrently(
        service,
        [(first_worker, charge_refunded("evt_p1a", first)),
         (second_worker, refund_updated("evt_p1b", first))],
    )
    deliver_concurrently(
        service,
        [(second_worker, charge_refunded("evt_p2a", first, second)),
         (first_worker, refund_updated("evt_p2b", second))],
    )
    want_state("after two colliding partials", service, "cus_9", 2, 4500)
    want("the amounts credited", service.amounts(), [1500, 3000])


def two_distinct_refunds_colliding_are_both_credited():
    """Two different refunds in flight together are two debts; neither may be swallowed."""
    service, (first_worker, second_worker) = build_workers(2)
    first = refund(refund_id="re_661", amount=2400, charge="ch_661")
    second = refund(refund_id="re_662", amount=600, charge="ch_662")
    deliver_concurrently(
        service,
        [(first_worker, charge_refunded("evt_d1", first)),
         (second_worker, refund_updated("evt_d2", second))],
    )
    want_state("after two colliding refunds", service, "cus_9", 2, 3000)
    want("the amounts credited", service.amounts(), [600, 2400])


def either_event_type_alone_credits_the_refund_in_full():
    """Not every refund is reported by both events; whichever one arrives has to credit it."""
    service, handler = build()
    handler.handle(charge_refunded("evt_only_charge", refund(refund_id="re_801", amount=3300)))
    want_state("charge.refunded alone", service, "cus_9", 1, 3300)

    service, handler = build()
    handler.handle(refund_updated("evt_only_refund", refund(refund_id="re_802", amount=3300)))
    want_state("refund.updated alone", service, "cus_9", 1, 3300)


def two_distinct_refunds_of_the_same_amount_are_both_credited():
    """Two returns of the same price are two refunds, not one reported twice."""
    first = refund(refund_id="re_701", amount=2500, charge="ch_701")
    second = refund(refund_id="re_702", amount=2500, charge="ch_702")
    service, (first_worker, second_worker) = build_workers(2)
    first_worker.handle(charge_refunded("evt_s1", first))
    second_worker.handle(refund_updated("evt_s2", first))
    second_worker.handle(charge_refunded("evt_s3", second))
    first_worker.handle(refund_updated("evt_s4", second))
    want_state("after two refunds of 2500", service, "cus_9", 2, 5000)
    want("the amounts credited", service.amounts(), [2500, 2500])


def a_refused_hand_over_leaves_the_refund_still_owed():
    """The store-credit service turns one hand-over away; the provider reports the refund again.

    A refund the endpoint could not hand over is still owed to the customer. Whatever the
    endpoint noted down about that refund must not outlive the money: the next delivery, on
    whichever worker, has to put the 3600 on the customer, and a delivery after that must not put
    it on a second time.
    """
    ref = refund(refund_id="re_901", amount=3600)
    service, (first_worker, second_worker) = build_workers(2)

    def refuse():
        raise RuntimeError("the store-credit service is not reachable")

    service._arm(refuse)
    try:
        first_worker.handle(charge_refunded("evt_f1", ref))
    except Exception:
        pass
    service._arm(None)
    want_state("after the refused hand-over", service, "cus_9", 0, 0)

    second_worker.handle(refund_updated("evt_f2", ref))
    want_state("after the provider reported it again", service, "cus_9", 1, 3600)

    first_worker.handle(charge_refunded("evt_f3", ref))
    want_state("after one more delivery of it", service, "cus_9", 1, 3600)


def an_unrelated_event_credits_nothing():
    service, handler = build()
    handler.handle(
        {"id": "evt_x", "type": "charge.succeeded", "data": {"object": {"id": "ch_501"}}}
    )
    want_state("after an unrelated event", service, "cus_9", 0, 0)


PROPERTIES = (
    ("two workers over one store-credit service colliding on one refund credit it once (3 runs)",
     two_workers_colliding_on_one_refund_credit_it_once),
    ("two workers over one store-credit service credit one refund once in sequence",
     two_workers_in_sequence_credit_one_refund_once),
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
    ("a second genuine refund handled by another worker is credited in full",
     a_second_genuine_refund_through_another_worker_is_credited_in_full),
    ("two partial refunds of one payment are both credited",
     two_partial_refunds_of_one_payment_are_both_credited),
    ("two partial refunds of one payment reaching different workers are both credited",
     two_partial_refunds_across_workers_are_both_credited),
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
    ("a refund the service refused to take is still credited when it is reported again",
     a_refused_hand_over_leaves_the_refund_still_owed),
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
