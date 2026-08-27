"""Totalling a batch of amounts that arrived as decimal strings.

A batch is one uploaded file: many lines, all in the same currency. The total
we compute here is the figure that gets reported back to the office that sent
the file, so it has to agree with adding the lines up by hand.
"""

from amounts import to_decimal_string, to_minor_units


def total_minor_units(currency, amounts):
    """Total a batch of decimal strings, in whole minor units."""
    total = 0
    for amount in amounts:
        total += to_minor_units(currency, amount)
    return total


def total_as_string(currency, amounts):
    """The batch total, rendered the way it appears on the report."""
    return to_decimal_string(currency, total_minor_units(currency, amounts))
