"""The two checks every amount, every currency and every counterparty call goes through.

An amount is an exact integer in minor units. A float is refused because 0.1 + 0.2 is not
0.3, and a bool is refused because `True` is an `int` in Python and is not one minor unit.
A currency is a canonical code, because an amount is not a number until it names its unit.

An `AuthorityScope` is the answer to "who exactly did we ask?": the provider, the account or
credential used, the region it was used in, and the identity of the backend that actually
holds the state. The labels are not enough on their own. Two independently created backends
can carry identical provider, account and region labels and still know nothing about each
other's keys, so a scope also names the backend, and a processor that was handed a scope
without one stamps its own. Same backend, same identity; different backend, different
identity, whatever the labels say.
"""
from __future__ import annotations

from dataclasses import dataclass


class InvalidAmount(ValueError):
    """Refused before anything is created or sent."""


class InvalidCurrency(ValueError):
    """Refused before anything is created or sent."""


class InvalidIdentifier(ValueError):
    """Refused before anything is created or sent."""


def validated_amount(value, what: str = "amount_minor") -> int:
    """An exact, positive integer number of minor units. Nothing else."""
    if isinstance(value, bool):
        raise InvalidAmount(f"{what} is {value!r}; a bool is not a number of minor units")
    if not isinstance(value, int):
        raise InvalidAmount(f"{what} is {value!r}, not an exact integer in minor units")
    if value <= 0:
        raise InvalidAmount(f"{what} is {value}; there is nothing to move")
    return value


def validated_component(value, what: str) -> int:
    """An exact, non-negative integer. A settled fee can consume the whole gross, leaving a
    net of zero, and zero is a real answer: refusing it would refuse a true settlement."""
    if isinstance(value, bool):
        raise InvalidAmount(f"{what} is {value!r}; a bool is not a number of minor units")
    if not isinstance(value, int):
        raise InvalidAmount(f"{what} is {value!r}, not an exact integer in minor units")
    if value < 0:
        raise InvalidAmount(f"{what} is {value}; a settled component is never negative")
    return value


def validated_identifier(value, what: str) -> str:
    """A non-empty string that names something. A list, a number, a bool, "" or "   " names
    nothing: none of them may become a ledger reference, an effect id, a dict key or a set
    member, because every one of those would then compare equal to something it is not."""
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise InvalidIdentifier(f"{what} is {value!r}, which does not name anything")
    return value


def validated_currency(value) -> str:
    """A non-empty canonical code. `""`, `None` and `"usd"` are refused, not normalised."""
    if not isinstance(value, str) or not value:
        raise InvalidCurrency(f"{value!r} is not a currency code")
    if len(value) != 3 or not (value.isalpha() and value.isupper()):
        raise InvalidCurrency(f"{value!r} is not a canonical three-letter currency code")
    return value


@dataclass(frozen=True)
class AuthorityScope:
    """Immutable. Who was asked, with which credential, in which region."""

    provider: str
    account: str
    region: str
    backend_id: str = ""    # which instance actually holds the state, not just its labels

    def fields(self) -> tuple:
        return (self.provider, self.account, self.region, self.backend_id)

    def describe(self) -> str:
        return (f"{self.provider}/{self.account}/{self.region}"
                f"#{self.backend_id or 'unbound'}")
