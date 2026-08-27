"""Currency metadata shared by everything that touches an amount.

How many decimal places a currency has is data, not a constant. Most of the
currencies we bill in have two, the yen and the won have none at all, and the
Gulf dinars have three. Reports, invoices and partner files all read the table
below rather than assuming every currency is shaped like the dollar.
"""

MINOR_UNIT_EXPONENTS = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CHF": 2,
    "JPY": 0,
    "KRW": 0,
    "BHD": 3,
    "KWD": 3,
    "TND": 3,
}


def normalise_code(code):
    """Return the canonical ISO code for a currency written any which way."""
    return str(code).strip().upper()


def is_supported(code):
    """True when we hold metadata for this currency."""
    return normalise_code(code) in MINOR_UNIT_EXPONENTS


def minor_unit_exponent(code):
    """How many decimal places `code` has. KeyError if the code is unknown."""
    return MINOR_UNIT_EXPONENTS[normalise_code(code)]
