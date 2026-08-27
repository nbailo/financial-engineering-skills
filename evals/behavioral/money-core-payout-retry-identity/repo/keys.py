"""Request ids for outbound calls."""

import itertools

_counter = itertools.count(1)


def new_request_id():
    """A fresh id for one outbound request. Never repeats within a process."""
    return "req-%06d" % next(_counter)
