"""A deterministic rendezvous for the two-worker tests.

Two SEPARATE workers run one call each, never two calls through one bound method, because
two calls through one object only ever prove same-instance safety. Each call receives a
`seam` that waits on a shared barrier, so the interleaving a concurrent duplicate needs
actually happens instead of being hoped for. Where a worker holds the shared lock across
that seam the other cannot arrive, the barrier times out, and BrokenBarrierError is
swallowed: that timeout is the guard working, not a failure.

Every join is BOUNDED and `Race.alive` names any thread still running afterwards, so a test
fails loudly instead of hanging or passing because a thread silently never finished.
`Race.seam_entries` counts how many workers actually got inside the window: one, where a
shared lock held the other out, and two where nothing did. Nothing here asserts on which
thread ran first.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

JOIN_TIMEOUT = 5.0
SEAM_TIMEOUT = 0.25


@dataclass
class Race:
    outcomes: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    alive: list = field(default_factory=list)
    seam_entries: int = 0       # how many workers reached the seam at all

    def raise_errors(self) -> None:
        for error in self.errors:
            if error is not None:
                raise error

    def sorted_outcomes(self) -> list:
        return sorted(self.outcomes, key=repr)


def race(*calls, join_timeout: float = JOIN_TIMEOUT,
         seam_timeout: float = SEAM_TIMEOUT) -> Race:
    """Run each call in its own thread. Every call takes one argument, the seam."""
    barrier = threading.Barrier(len(calls), timeout=seam_timeout)
    counter = threading.Lock()
    result = Race([None] * len(calls), [None] * len(calls), [])

    def seam() -> None:
        with counter:
            result.seam_entries += 1
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass

    def run(index, call) -> None:
        try:
            result.outcomes[index] = call(seam)
        except BaseException as exc:            # recorded, re-raised on the main thread
            result.errors[index] = exc

    threads = [threading.Thread(target=run, args=(i, c), daemon=True)
               for i, c in enumerate(calls)]
    for thread in threads:
        thread.start()
    for index, thread in enumerate(threads):
        thread.join(join_timeout)
        if thread.is_alive():
            result.alive.append(index)
    return result
