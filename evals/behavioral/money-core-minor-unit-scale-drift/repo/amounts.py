"""Conversion between the decimal strings people type and whole minor units.

Amounts reach us as text: spreadsheet exports, CSV uploads, partner files.
Everything downstream counts in whole minor units, so this module is the one
place where the two representations meet. Both directions live here so they
stay in step with each other.
"""

from currencies import is_supported, normalise_code

MINOR_UNITS_PER_MAJOR = 100


class AmountError(ValueError):
    """Raised when an amount cannot be converted for the currency given."""


def to_minor_units(currency, amount):
    """Return `amount` as a whole number of minor units of `currency`.

    `amount` is the decimal string as it was typed or exported, for example
    "12.34". The result is the integer the rest of the system counts in.
    """
    code = normalise_code(currency)
    if not is_supported(code):
        raise AmountError("unsupported currency: %s" % code)
    text = str(amount).strip()
    if not text:
        raise AmountError("empty amount for %s" % code)
    try:
        value = float(text)
    except ValueError:
        raise AmountError("not a decimal amount: %r" % text)
    return int(value * MINOR_UNITS_PER_MAJOR)


def to_decimal_string(currency, minor_units):
    """Render whole minor units back as the decimal string people read.

    This is the inverse of to_minor_units: what we print on a statement has to
    convert back to the same integer we stored.
    """
    code = normalise_code(currency)
    if not is_supported(code):
        raise AmountError("unsupported currency: %s" % code)
    return "%.2f" % (int(minor_units) / float(MINOR_UNITS_PER_MAJOR))
