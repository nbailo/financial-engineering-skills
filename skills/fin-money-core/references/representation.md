# Representation

The mechanism behind *exact representation*: which concrete type holds an obligation in each language, which
column type holds it in each database, and the context that stops a division leaking a residue. A value a
counterparty can demand lives its whole life exact; a value nobody can demand is an estimate, and binary
floating point is the correct type for it. Read this before you declare the column or the struct field. Scale
and currency travel in `currency-and-scale.md`; the boundaries that degrade a correct value on its way out
are in `serialization-and-width.md`.

## Contents

- [The obligation/estimate boundary, per language](#the-obligationestimate-boundary-per-language)
- [Column types: Postgres and MySQL](#column-types-postgres-and-mysql)
- [Decimal context: `prec` and `traps[Inexact]`](#decimal-context-prec-and-trapsinexact)
- [An instant is a quantity, and the business date is derived from it](#an-instant-is-a-quantity-and-the-business-date-is-derived-from-it)
- [The test this reference owes](#the-test-this-reference-owes)

---

## The obligation/estimate boundary, per language

The observable predicate is **"can a counterparty send you a message that says *you owe me this number*?"**
If yes, it is an obligation: integer minor units, a scaled integer with a declared exponent, or an
arbitrary-precision decimal constructed only from `str`/`int`. If no (greeks, implied vol, VaR, Monte Carlo
paths, backtest statistics, ML features, chart coordinates), binary64 is correct, because the functions those
values need (`exp`, `ln`, `sqrt`, `Φ`, matrix decompositions, root-finders) are inexact in every radix and
model error dominates 1e-16 by ten or more orders of magnitude.

| Language | Obligation type | Intermediate math | Constructor that imports error | Comparison trap |
|---|---|---|---|---|
| **Python** | `int` minor units, or `decimal.Decimal` built **only** from `str`/`int` | `Decimal` inside an explicit `localcontext(prec=…)`, one `quantize()` at the boundary | `Decimal(0.1)` → `Decimal('0.1000000000000000055511151231257827021181583404541015625')` | none for `==`; `Decimal('1.30')+Decimal('1.20')` is `2.50`, trailing zeros are significant |
| **TypeScript/JS** | `bigint` minor units, or Dinero.js v2 / `big.js` / `decimal.js` | `big.js`/`decimal.js`, never `number` | `Number(x)`, `parseFloat`, `JSON.parse` of a numeric amount | `toFixed()` rounds the *float's* value: `(1.005).toFixed(2) === "1.00"` |
| **Go** | `int64`/`int128` minor units, or `shopspring/decimal` | `shopspring/decimal`; `Div` reads a **package-global** `DivisionPrecision`; use `DivRound`/`RoundBank` with an explicit precision per call | `decimal.NewFromFloat(x)` | scan nullable columns as `decimal.NullDecimal`, not `*float64` |
| **Rust** | `i128`/`u128` minor units, or `rust_decimal::Decimal` | `rust_decimal` `checked_*` variants; `num-rational` for intermediate pro-rata | `Decimal::from_f64_retain` | bounded type; see `serialization-and-width.md` |
| **Java** | `long` minor units, `BigDecimal` from `String`, or JSR-354 `Money` | `BigDecimal` with an explicit `MathContext` **and** an explicit `RoundingMode` on every `divide`/`setScale` | `new BigDecimal(0.1)` | `new BigDecimal("2.0").equals(new BigDecimal("2.00"))` is **false**; use `compareTo` |
| **C#** | `long` minor units, or `decimal` | `decimal` with `Math.Round(x, n, MidpointRounding.…)`; the argument is not optional in a money path | `(decimal)someDouble` | `Math.Round` without `MidpointRounding` silently uses banker's (`ToEven`) |

Two carve-outs that stop this from being the useless "never use a float" rule:

**Transport encoding.** Where a counterparty's published schema types the field as a float, the float is a
serialization step and nothing else. Deribit `/private/buy` declares `amount` and `price` as JSON `number`
(<https://docs.deribit.com/api-reference/trading/private-buy.md>); IBKR TWS `IBApi.Order` declares
`LmtPrice` and `AuxPrice` as `double` while `TotalQuantity` is `decimal`
(<https://interactivebrokers.github.io/tws-api/classIBApi_1_1Order.html>). Compute and store exact, convert
once at the boundary, assert the encoding round-trips (`Decimal(repr(f)) == exact`), and re-derive the
authoritative record from the venue's own decimal/string echo, never from the float you sent.

**Book of record for nobody.** freqtrade declares every money field as SQL `Float`
(`trade_model.py:95-117`, `verified-source-code.md` §6.2) while aggregating in `ccxt.Precise` string math and
casting back for storage. That is defensible *there* because freqtrade is an authority for no third party and
can re-derive every aggregate from the order rows. The boundary is "is this an authority for someone else",
not "is this finance". Once a `user_id` appears on the row, the carve-out is gone.

---

## Column types: Postgres and MySQL

| Engine | Use | Never use | Why |
|---|---|---|---|
| **PostgreSQL** | `NUMERIC(p,s)` with `p` and `s` both declared, **or** `BIGINT`/`NUMERIC(38,0)` minor units, always beside a `currency` column | `REAL`, `DOUBLE PRECISION`, and PG's own `money` | PG documents `numeric` as "especially recommended for storing monetary amounts and other quantities where exactness is required"; the `money` page says casting from `real`/`double precision` "is not recommended. Floating point numbers should not be used to handle money due to the potential for rounding errors", and `money` output is `lc_monetary`-dependent and truncates on integer division (<https://www.postgresql.org/docs/current/datatype-money.html>) |
| **MySQL** | `DECIMAL(p,s)` with both declared, or `BIGINT` minor units + currency | `FLOAT`, `DOUBLE` | `DECIMAL` is exact fixed point; `FLOAT`/`DOUBLE` are binary64 with the same 1/10 failure. *(MySQL's maximum `p` and `s` are not established by the sources read for this file; check the server docs before choosing them.)* |
| **SQL Server** | `DECIMAL(19,4)` or wider | `MONEY`, `SMALLMONEY`, `FLOAT` | `money`/`smallmoney` are fixed scale 4 **and truncate intermediate division results to scale 4 before subsequent operations**, so `@m/10*10 ≠ @m` (secondary: Red Gate BP022; reproduce before quoting) |

Three Postgres specifics that decide a schema review:

- **Declare `p` and `s`.** Bare `NUMERIC` does not coerce input to any scale: `0.10` and `0.100` are both
  stored as written, compare `=`, and differ under `::text`. That is right for a system preserving
  significance and wrong for one that hashes, text-compares, or checksums amounts for reconciliation. On a
  ledger, the canonical-form property is worth more.
- **`NUMERIC(p,s)` rounds the fraction and errors on the integer part.** An INSERT with more fractional
  digits than `s` is silently rescaled; an INSERT that exceeds `p` raises. Declaring `p` is what converts a
  range violation from a wrong number into an aborted transaction.
- **`numeric` NaN sorts as equal to itself and greater than every non-NaN**, deviating from IEEE 754. Any
  ordering or `MAX()` over a money column that can hold NaN is not doing what the reader thinks
  (<https://www.postgresql.org/docs/current/datatype-numeric.html>).

A correct column is undone by the driver. Many drivers hand back a native float for `NUMERIC` unless
configured (`mysql-connector`, some `sqlite3` adapters; raw JS Postgres clients return strings, which is
safe, until an ORM coerces to `Number`). **Assert the returned language-level type**, not the value:

```python
def test_amount_column_returns_decimal(session):
    session.execute(text("INSERT INTO postings (id, amount, currency) VALUES (1, '0.10', 'USD')"))
    session.commit()
    (amount,) = session.execute(text("SELECT amount FROM postings WHERE id = 1")).one()
    assert type(amount) is Decimal        # the load-bearing line: float 0.5 would pass a value assert
    assert amount == Decimal("0.10")
    assert str(amount) == "0.10"          # declared scale survived the round trip; a float gives "0.1"
```

The value assertion alone is not enough: `Decimal("0.50") == 0.5` is `True`, so a driver returning floats
passes every test written with a value ending in a power of two.

---

## Decimal context: `prec` and `traps[Inexact]`

**Switching from float to `Decimal` removes representation error, not rounding error.** Microsoft says it in
the `System.Decimal` docs verbatim: "The Decimal type does not eliminate the need for rounding. Rather, it
minimizes errors due to rounding. For example, the following code produces a result of
0.9999999999999999999999999999 instead of 1." Reproduced in Python at the default 28-digit context:

```
Decimal(1) / Decimal(3) * 3   ->  Decimal('0.9999999999999999999999999999')
```

A rule that governs construction and storage but says nothing about division leaves that silent. So: every
inexact operation (division, `**`, any scale conversion) executes inside a context with a **declared
`prec`** and **`traps[Inexact]` set**, and rounding is permitted in exactly one named function.

```python
from contextlib import contextmanager
from decimal import Decimal, localcontext, Inexact, Rounded, ROUND_HALF_UP, ROUND_FLOOR

@contextmanager
def exact():
    """+, -, *, comparisons. Any silent rounding raises decimal.Inexact instead of leaking a residue."""
    with localcontext() as ctx:
        ctx.prec = 50
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        yield

def quantize(value: Decimal, exponent: int, mode: str) -> Decimal:
    """The ONE place rounding is allowed. exponent from scale_for(); mode from policy, never a default."""
    with localcontext() as ctx:
        ctx.prec = 50
        ctx.traps[Inexact] = False
        ctx.traps[Rounded] = False
        return value.quantize(Decimal(1).scaleb(-exponent), rounding=mode)
```

Inside `exact()`, `Decimal("1") / Decimal("3")` raises `decimal.Inexact` at the line that caused it rather
than returning `0.333…` and losing a residue four call frames later. That is Java's `RoundingMode.UNNECESSARY`
(which throws `ArithmeticException` when the result is inexact) ported to Python, and it is the single
most under-used correctness tool in either runtime.

Language-specific mechanics of the same idea:

| Language | Declare the precision | Force inexactness to raise |
|---|---|---|
| Python | `localcontext(); ctx.prec = N`: the context is **thread-local**, so a `getcontext().prec` set at import does not reach threads started later | `ctx.traps[Inexact] = True` |
| Java | an explicit `MathContext` on every `divide`/`setScale` | `RoundingMode.UNNECESSARY`; also, `divide` with **no** rounding mode already throws on a non-terminating expansion |
| C# | `Math.Round(x, n, MidpointRounding.…)` | no equivalent trap; compare against the unrounded value explicitly |
| Go | `decimal.DivRound(d, precision)` per call; do not rely on the package-global `DivisionPrecision` | no equivalent trap |
| Rust | `rust_decimal` `checked_div` / `round_dp_with_strategy` | no equivalent trap; the bounded range makes `checked_*` mandatory anyway |

The retail-scale proof that this matters, from Cowlishaw's telco example
(<https://speleotrove.com/decimal/decifaq1.html>): 5% tax on a $0.70 call, rounded to the cent.

```
0.70 * 1.05  (binary64)  -> 0.7349999999999999  ;  round(...,2) -> 0.73     ✗
Decimal("0.70") * Decimal("1.05") = 0.7350 ; quantize(0.01, HALF_UP) -> 0.74 ✓
```

One cent per call, one direction. Cowlishaw: "Taken over a million transactions of this kind … these
systematic errors add up to an overcharge of more than $20 … over a whole year the error then exceeds
$5 million."

---

## An instant is a quantity, and the business date is derived from it

A timestamp on a money path is a represented quantity like any other: it has a type, a zone that
plays the role of scale, and an authority that fixes it. Getting it wrong moves an obligation into
the wrong period rather than changing its amount, which is why it survives review.

Every money-path timestamp is timezone-aware UTC with an explicit type, and the business date derives from a named cutoff in a
named timezone. Funding intervals, settlement dates, accrual periods, statement cutoffs, failure windows and retention bounds
all key on it, and events across nodes are ordered by a sequence, never a wall clock.

**Shape**

```
instant -> timezone-aware UTC, explicit type; business date -> cutoff(named time, named zone) applied to it
```

**How it appears** `date.today()`, a naive `datetime.now()`, `utcnow()` with no tzinfo, a `TIMESTAMP` column with no time zone.

Funding intervals, settlement dates, accrual periods, statement cutoffs, failure windows and
retention bounds all key on the business date, so the cutoff time and its zone are configuration,
per jurisdiction and per instrument, never a constant in the function that reads the clock. Events
originating on different nodes are ordered by a sequence the writer controls, never by a wall clock:
two clocks that agree to a millisecond still disagree about which of two postings came first.

---

## The test this reference owes

1. **The driver/ORM type assertion**, per money column, as written above. Float money rarely enters through
   *arithmetic* any more; it enters through a column type: freqtrade still declares every money field as SQL
   `Float` (`trade_model.py:95-117`).
