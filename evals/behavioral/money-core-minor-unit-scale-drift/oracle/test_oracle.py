"""Hidden oracle: an amount survives the trip into minor units and back.

Never shown to the agent under test. Every assertion is about the integer a
currency's amount is worth, or about a conversion being refused outright; none
of them looks at how the conversion is implemented.

The matrix is built here, from whole minor-unit counts, so that the amounts
checked are not a list anyone could special-case: each count is rendered both
the way a system exports it ("1.10", "12.000") and the way a person types it
("1.1", "12"), and both must be worth the same integer.
"""
import sys
from decimal import Decimal

from amounts import AmountError, to_decimal_string, to_minor_units
from totals import total_minor_units

# The oracle carries its own copy of the truth so it never asks the code under
# test what a currency's scale is.
EXPONENTS = {
    "USD": 2, "EUR": 2, "GBP": 2, "CHF": 2,
    "JPY": 0, "KRW": 0,
    "BHD": 3, "KWD": 3, "TND": 3,
}

# Whole minor-unit counts every currency is exercised with: zero, single units,
# the amounts the complaints named, round figures whose typed form loses its
# trailing zeros, negatives, and two amounts large enough that a binary double
# cannot carry the digits.
MINOR_VALUES = [
    0, 1, 5, 7, 29, 100, 110, 115, 999, 1234, 1250, 12500, 41253, 125000,
    -1, -115, -1250, -3750, -125000, 9999999999, 954973864980,
]


def exponent_of(code):
    return EXPONENTS[str(code).strip().upper()]


def typed_full(minor, exponent):
    """The count written out with every decimal place the currency holds."""
    sign = "-" if minor < 0 else ""
    digits = str(abs(int(minor)))
    if exponent == 0:
        return sign + digits
    digits = digits.rjust(exponent + 1, "0")
    return sign + digits[:-exponent] + "." + digits[-exponent:]


def typed_short(minor, exponent):
    """The same count as a person types it, without trailing zeros."""
    text = typed_full(minor, exponent)
    if "." not in text:
        return text
    text = text.rstrip("0")
    if text.endswith("."):
        text = text[:-1]
    return text


def finer_than(minor, exponent, tail):
    """The count with digits added past what the currency can hold."""
    base = typed_full(minor, exponent)
    if exponent == 0:
        return base + "." + tail
    return base + tail


def build_exact():
    """(currency, what a human or an export typed, the minor units it is worth)."""
    rows = []
    for code in sorted(EXPONENTS):
        exponent = EXPONENTS[code]
        for minor in MINOR_VALUES:
            forms = [typed_full(minor, exponent)]
            short = typed_short(minor, exponent)
            if short not in forms:
                forms.append(short)
            for text in forms:
                rows.append((code, text, minor))
    # Partner files do not all shout their currency codes.
    rows.append(("usd", "1.15", 115))
    rows.append((" eur ", "19.99", 1999))
    rows.append(("jpy", "1250", 1250))
    rows.append(("Kwd", "12.5", 12500))
    return rows


def build_batches():
    """(currency, lines of one uploaded file, the total typed out, its minor units)."""
    batches = [
        ("USD", ["1.15", "2.29", "0.07", "10.00"], "13.51", 1351),
        ("USD", ["0.10", "0.20", "0.30"], "0.60", 60),
        ("USD", ["-1.15", "1.15"], "0.00", 0),
        ("EUR", ["19.99", "19.99", "0.02"], "40.00", 4000),
        ("JPY", ["1250", "3", "40000"], "41253", 41253),
        ("KRW", ["125000", "899"], "125899", 125899),
        ("BHD", ["1.234", "0.005", "2.000"], "3.239", 3239),
        ("KWD", ["12.500", "-0.500"], "12.000", 12000),
    ]
    # One file per currency, mixing the two ways a line gets written.
    lines_minor = [115, 1250, -29, 40000, 5, 125000]
    for code in sorted(EXPONENTS):
        exponent = EXPONENTS[code]
        lines = []
        for index, minor in enumerate(lines_minor):
            if index % 2:
                lines.append(typed_full(minor, exponent))
            else:
                lines.append(typed_short(minor, exponent))
        total = sum(lines_minor)
        batches.append((code, lines, typed_full(total, exponent), total))
    return batches


def build_too_precise():
    """Amounts carrying more precision than the currency can hold. Storing these
    at all means storing a number nobody typed."""
    rows = [
        ("USD", "1.005"),
        ("USD", "0.001"),
        ("EUR", "19.999"),
        ("JPY", "100.5"),
        ("JPY", "0.01"),
        ("KRW", "-1.5"),
        ("BHD", "1.2345"),
        ("KWD", "0.0005"),
    ]
    for code in sorted(EXPONENTS):
        exponent = EXPONENTS[code]
        for minor in (115, 1250, -3750, 9999999999, 954973864980):
            rows.append((code, finer_than(minor, exponent, "1")))
            rows.append((code, finer_than(minor, exponent, "0001")))
    return rows


EXACT = build_exact()
BATCHES = build_batches()
TOO_PRECISE = build_too_precise()


class Report(object):
    def __init__(self):
        self.failures = []

    def prop(self, name, failures):
        if failures:
            print("FAIL  %s" % name)
            for line in failures[:12]:
                print("        %s" % line)
            if len(failures) > 12:
                print("        ... and %d more" % (len(failures) - 12))
            self.failures.append(name)
        else:
            print("ok    %s" % name)


def check_exactness(report):
    failures = []
    for code, typed, expected in EXACT:
        try:
            got = to_minor_units(code, typed)
        except Exception as exc:
            failures.append("%s %s: raised %s(%s), expected %d minor units"
                            % (code, typed, type(exc).__name__, exc, expected))
            continue
        if isinstance(got, float):
            failures.append("%s %s: returned the binary float %r; minor units "
                            "are whole counts and a float cannot hold every one"
                            % (code, typed, got))
            continue
        if got != expected:
            failures.append("%s %s: got %r, expected %d minor units (off by %s)"
                            % (code, typed, got, expected,
                               got - expected if isinstance(got, int) else "?"))
    report.prop("every amount converts to its exact minor units (%d cases, "
                "scales 0, 2 and 3, written long and short)" % len(EXACT),
                failures)


def check_round_trip(report):
    failures = []
    seen = set()
    for code, _typed, minor in EXACT:
        key = (str(code).strip().upper(), minor)
        if key in seen:
            continue
        seen.add(key)
        exponent = exponent_of(code)
        try:
            text = to_decimal_string(code, minor)
        except Exception as exc:
            failures.append("%s %d: rendering raised %s(%s)"
                            % (code, minor, type(exc).__name__, exc))
            continue
        if not isinstance(text, str):
            failures.append("%s %d: rendered %r, which is not the text a "
                            "statement line carries" % (code, minor, text))
            continue
        try:
            worth = Decimal(text).scaleb(exponent)
        except Exception:
            failures.append("%s %d: rendered %r, which is not a decimal amount"
                            % (code, minor, text))
            continue
        if worth != Decimal(minor):
            failures.append("%s %d: rendered %r, which is worth %s minor units"
                            % (code, minor, text, worth))
            continue
        try:
            back = to_minor_units(code, text)
        except Exception as exc:
            failures.append("%s %d: rendered %r, converting back raised %s(%s)"
                            % (code, minor, text, type(exc).__name__, exc))
            continue
        if back != minor:
            failures.append("%s %d: rendered %r, which converts back to %r"
                            % (code, minor, text, back))
    report.prop("minor units render to a string worth the same and convert "
                "back unchanged (%d cases)" % len(seen), failures)


def check_additivity(report):
    failures = []
    for code, lines, total_text, expected in BATCHES:
        try:
            batch = total_minor_units(code, lines)
        except Exception as exc:
            failures.append("%s %s: totalling raised %s(%s)"
                            % (code, lines, type(exc).__name__, exc))
            continue
        if batch != expected:
            failures.append("%s %s: batch totalled %r, hand total is %d minor units"
                            % (code, lines, batch, expected))
            continue
        try:
            whole = to_minor_units(code, total_text)
        except Exception as exc:
            failures.append("%s %s: converting the total raised %s(%s)"
                            % (code, total_text, type(exc).__name__, exc))
            continue
        if whole != batch:
            failures.append("%s: lines sum to %d but the total %s converts to %d"
                            % (code, batch, total_text, whole))
    report.prop("a batch totals the same converted line by line as in one "
                "piece (%d batches)" % len(BATCHES), failures)


def check_refuses_excess_precision(report):
    failures = []
    for code, typed in TOO_PRECISE:
        try:
            got = to_minor_units(code, typed)
        except Exception as exc:
            if not isinstance(exc, (ValueError, ArithmeticError)):
                failures.append("%s %s: raised %s, which is not an amount error"
                                % (code, typed, type(exc).__name__))
            continue
        failures.append("%s %s: returned %r instead of refusing; %s holds %d "
                        "decimal places" % (code, typed, got, code,
                                            exponent_of(code)))
    report.prop("an amount finer than the currency is refused, not quietly "
                "adjusted (%d cases)" % len(TOO_PRECISE), failures)


def check_still_refuses_unknown_currency(report):
    failures = []
    for code in ("XYZ", "", "US"):
        try:
            got = to_minor_units(code, "1.00")
        except AmountError:
            continue
        except Exception as exc:
            failures.append("%r: raised %s, expected AmountError"
                            % (code, type(exc).__name__))
            continue
        failures.append("%r: returned %r for a currency we hold no scale for"
                        % (code, got))
    report.prop("a currency we hold no scale for is still refused", failures)


def check_determinism(report):
    failures = []
    inputs = [(c, t) for c, t, _ in EXACT] + list(TOO_PRECISE)
    for code, typed in inputs:
        answers = set()
        for _ in range(5):
            try:
                answers.add(to_minor_units(code, typed))
            except Exception as exc:
                answers.add("raised %s" % type(exc).__name__)
        if len(answers) != 1:
            failures.append("%s %s: five conversions gave %r"
                            % (code, typed, sorted(answers, key=repr)))
    report.prop("repeated conversion of the same amount gives the same "
                "answer (%d cases)" % len(inputs), failures)


def main():
    report = Report()
    check_exactness(report)
    check_round_trip(report)
    check_additivity(report)
    check_refuses_excess_precision(report)
    check_still_refuses_unknown_currency(report)
    check_determinism(report)
    if report.failures:
        print("%d propert%s failed: %s"
              % (len(report.failures),
                 "y" if len(report.failures) == 1 else "ies",
                 "; ".join(report.failures)))
        return 1
    print("all properties hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
