# Representation

The mechanism behind MC1 and MC2: which concrete type holds an obligation in each language, which column
type holds it in each database, where the scale comes from, and the six boundaries at which a correct
in-memory value is silently degraded on its way to storage or to a counterparty. Everything here assumes the
obligation/estimate split is already the question: a value a counterparty can demand is an obligation and
lives its whole life exact; a value nobody can demand is an estimate and binary floating point is the
correct type for it. Read this before you declare the column, the struct field, or the protobuf message.

## Contents

- [The obligation/estimate boundary, per language](#the-obligationestimate-boundary-per-language)
- [Column types: Postgres and MySQL](#column-types-postgres-and-mysql)
- [ISO 4217 exponents, and the currencies that are not 2](#iso-4217-exponents-and-the-currencies-that-are-not-2)
- [Four scales for one currency at one vendor](#four-scales-for-one-currency-at-one-vendor)
- [Decimal context: `prec` and `traps[Inexact]`](#decimal-context-prec-and-trapsinexact)
- [Serialization boundaries where precision dies](#serialization-boundaries-where-precision-dies)
- [Integer width and overflow](#integer-width-and-overflow)
- [Currency as a type; FX as an explicit operation](#currency-as-a-type-fx-as-an-explicit-operation)
- [The four tests this reference owes](#the-four-tests-this-reference-owes)

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
| **Rust** | `i128`/`u128` minor units, or `rust_decimal::Decimal` | `rust_decimal` `checked_*` variants; `num-rational` for intermediate pro-rata | `Decimal::from_f64_retain` | bounded type; see [overflow](#integer-width-and-overflow) |
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

## ISO 4217 exponents, and the currencies that are not 2

`amount * 100` is wrong in four independent ways. The exponent table is **data with a version**, not a
constant: Dinero.js states that it "tracks the ISO 4217 standard and updates currencies when amendments are
published" (<https://www.dinerojs.com/docs/core-concepts/currency>).

| Exponent | Codes | Worked failure of a hardcoded `×100` |
|---|---|---|
| **0** | JPY, KRW, VND, CLP, ISK, UGX, PYG, RWF, BIF, DJF, GNF, KMF, VUV, XOF, XAF, XPF | ¥100 stored as `10000` is a **100× overcharge** |
| **2** | most | none |
| **3** | BHD, KWD, JOD, OMR, TND, IQD, LYD | 1.234 KWD is `1234` fils; forcing 2 decimals drops the third digit **and** misprices by 10× |
| **4** | CLF (Chilean Unidad de Fomento) | an inflation-indexed unit of account, and a **funds code**, so any "code ⇒ physical currency" assumption also breaks |
| **non-decimal** | MGA, MRU subdivide by **5**, not by a power of ten (ISO's minor-unit column still lists 2, a documented disconnect) | a base-10 exponent cannot express the subdivision at all |
| **no minor unit** | XAU/XAG/XPT/XPD are one troy ounce of metal; XXX means "no currency"; XTS is reserved for testing | a `Money` type keyed on ISO code will happily invent cents for gold |

Sources: `money-representation.md` src 21 and `payments-processors.md` src 35, cross-checked against
Stripe's zero-decimal list (<https://docs.stripe.com/currencies.md>) and datatrans currency codes. The ISO
list itself is maintained by SIX on behalf of ISO and is paywalled; the exponent sets above are the
cross-checked secondary consensus, not a quote from the standard.

Two consequences that are schema decisions, not runtime ones:

1. **The scale travels with the amount.** TigerBeetle's `ledger` field partitions accounts by asset and only
   accounts on the same ledger transact directly; Formance encodes the scale into the asset identifier
   itself (`"USD/2"`). An integer amount with no `(currency, scale)` pair beside it is uninterpretable.
2. **Scale is immutable once rows exist.** TigerBeetle: "asset scales cannot be changed after account
   creation without migrating to a new ledger" (<https://docs.tigerbeetle.com/coding/data-modeling/>).
   Widening `NUMERIC(12,2)` to `NUMERIC(12,4)` while the application changes what it writes reinterprets
   every historical row with no error and no signal. Changing a scale is a data migration with a backfill,
   never an `ALTER COLUMN`.

Downstream, the exponent is also a **validation** rule: ISO 20022 requires the number of fractional digits to
comply with ISO 4217 for the stated currency, and `<InstdAmt Ccy="EUR">10.403</InstdAmt>` is rejected with
"Too many decimal digits given. Maximum of 2 may be present for the given currency". Round at the currency's
exponent *before* serialising, and post the residue somewhere named. (Secondary: xmldation quoting the
standard, `payments-processors.md` src 36; not read from the ISO 20022 e-Repository.)

---

## Four scales for one currency at one vendor

Charge scale, payout scale, display scale and calculation scale are four possibly-different numbers for the
same currency at the same vendor. All four are runtime metadata.

| Scale | Who defines it | Evidence | Failure if you use another scale here |
|---|---|---|---|
| **Charge** | the processor's create-charge API | Stripe: HUF and TWD are chargeable at **2** decimals | reject or 100× |
| **Payout** | the processor's payout rails | Stripe: HUF/TWD **payout amounts must be evenly divisible by 100**; ISK and UGX transitioned to zero-decimal but "backward compatibility requires you to represent it as a two-decimal value, where the decimal amount is always `00`": 5 ISK is `500` | a HUF 10.45 balance **cannot be paid out in full**; an ISK amount not ending in `00` is rejected |
| **Calculation** | the venue's own ledger | Kraken exposes `decimals` on the Assets endpoint: **BTC 10**, USD 4 | your locally computed number will not reconcile with the exchange's ledger |
| **Display** | the venue's UI convention | Kraken exposes `display_decimals`: **BTC 5**, USD 2 | display precision used as calculation precision truncates 5 significant digits off every BTC figure |

Stripe currencies: <https://docs.stripe.com/currencies.md>. Kraken decimal precision:
<https://support.kraken.com/articles/201988998-decimal-precision-for-api-calculations>.

Stripe also documents the residue policy for the exponent-0-encoded-as-2 currencies: where proration,
coupons or tax produce a fractional UGX amount, "Stripe automatically rounds that amount to the nearest
number evenly divisible by 100. We credit or debit any difference from rounding to the customer balance."
The residue is posted to a real account, not discarded; copy that shape.

There is also a **wire-format ceiling** that is neither the type's range nor the currency's exponent: Stripe
bounds amounts by *digit count*: 12 digits for most card rails, 9 for Amex, 8 for most non-card methods,
with tighter per-network and per-region caps. A cart total that fits `BIGINT` can still be unpayable.

The resolver, not a constant:

```python
@dataclass(frozen=True)
class Scale:
    exponent: int            # digits after the point in the wire representation
    multiple_of: int = 1     # minor-unit granularity: 100 for Stripe HUF/TWD payouts and ISK/UGX

def scale_for(currency: str, purpose: Literal["charge", "payout", "display", "calculation"]) -> Scale:
    """Resolved from cached vendor metadata + the ISO exponent table. Never a literal 2."""
```

Every call site names the purpose. A `to_minor(amount, currency)` with no purpose argument is the defect.

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

## Serialization boundaries where precision dies

The arithmetic is rarely where this fails. These six are.

| Boundary | Mechanism | What the value becomes | Correct shape |
|---|---|---|---|
| **JSON number** | RFC 8259 §6: "good interoperability can be achieved by implementations that expect no more precision or range than IEEE 754 binary64 … integers in the range [-(2\*\*53)+1, (2\*\*53)-1] are interoperable". Damage happens **inside the parser** | `JSON.parse('{"amount":0.1}')` yields the double `0.1000000000000000055511…`; `JSON.parse("12345678901234567890")` yields `12345678901234567000` | amounts as **strings** or integer minor units. `Decimal(str(json_number))` is *not* a fix: `str()` of an already-damaged double is already wrong |
| **ORM / driver coercion** | a correct `NUMERIC(18,8)` handed back as `float64` / JS `Number` | scale and exactness gone after a correct write | assert the returned **type** in a test (see above) |
| **protobuf `double`** | identical to binary64 | same as JSON | `google.type.Money` `{currency_code, units:int64, nanos:int32}`, a string field, or `{scaled_int, scale}`. Note `Money`'s fixed 10⁻⁹ scale **cannot** hold an 18-decimal token amount |
| **Avro / Parquet `decimal`** | `precision` and `scale` live in the **schema**; the payload is only the two's-complement big-endian unscaled integer | a schema evolution changing `scale` from 2 to 4 reinterprets every historical record as 1/100 of its true value, with no error | treat `scale` as immutable once data exists |
| **CSV / Excel** | Excel stores 15 significant decimal digits and zeroes everything beyond (<https://learn.microsoft.com/en-us/troubleshoot/microsoft-365-apps/excel/floating-point-arithmetic-inaccurate-result>); CSV carries no type at all | a 19-digit amount or a leading-zero account number becomes a float on open | never route money through a spreadsheet inside an automated pipeline |
| **Float SQL column** | `REAL` / `DOUBLE PRECISION` / `FLOAT` rounds on INSERT | correct in memory, wrong at rest | see the column table above |

Two more that show up as one-line diffs:

- **`BigInt` ↔ JSON.** `JSON.stringify(1n)` throws `TypeError`; the reflex fix is `Number(bigint)`, which
  reintroduces the 2⁵³ ceiling at the serializer. Serialize `bigint` amounts with an explicit
  `.toString()` and a schema that says the field is a decimal string.
- **Display formatting used as computation.** `Number(x.toFixed(2))`, `float(f"{x:.2f}")`,
  `parseFloat(formatted)` all round the *float's* actual value and hand back a float. Formatting is the last
  step before a human reads the number, never an arithmetic step. `toLocaleString` additionally emits
  locale group separators (`1.234,56`, non-breaking space) that no parser accepts.

Two documented positives worth copying. Kraken's spot WS book v2 instructs clients to "Parse `price` and
`qty` fields using a decimal or string decoder to preserve full precision through deserialisation", and the
book checksum is computed over the **string** forms, so a JSON parser yielding floats breaks the checksum,
which makes the float bug **detectable at runtime** rather than silent
(<https://docs.kraken.com/api/docs/guides/spot-ws-book-v2/>). Nasdaq TotalView-ITCH puts prices on the wire
as `Price(4)` fixed-point integers with four implied decimals; no float exists in the format.

And the counterexample that shows the pattern is conditional, not universal: the Bitcoin JSON-RPC mitigation
`int64(round(value * 1e8))` is sound only because 21e6 × 1e8 = 2.1e15 < 2⁵³ ≈ 9.007e15, so every satoshi
value is exactly representable in binary64. **The identical line applied to an 18-decimal ERC-20 is
catastrophic.** The predicate to test is `max_value × 10^scale < 2^53`.

---

## Integer width and overflow

Choose the width from the worst-case product of range and scale, and prove the bound in a test or a comment
next to the type.

| Width | At scale 2 (cents) | At scale 18 (wei) | Note |
|---|---|---|---|
| `2^53` (JS `number`, JSON) | $90,071,992,547,409.92 | 0.009007 tokens | exact for *integers* only; any `*` or `/` leaves the safe set |
| `int64` (`2^63−1`) | ≈ $9.22e16 | 9.22 tokens | comfortable for fiat cents and for satoshis |
| `uint64` (`2^64−1`) | n/a | **18.446744073709553 tokens**: a `u64` cannot hold 19 ETH in wei | this single fact is why EVM values are `uint256` |
| `u128` | n/a | 3.4e38 minor units | TigerBeetle's choice; 1M transfers/sec for 1000 years at micro-cent scale ≈ 3.154e36 |
| `uint256` | n/a | ≈ 1.158e59 tokens | why 18-decimal tokens are viable at all |
| .NET `decimal`, `rust_decimal` | ≈ 7.9228e28 total (`OVERFLOW_U96` = 79,228,162,514,264,337,593,543,950,336) | **bounded: these overflow.** Python `Decimal`, Java `BigDecimal` and unconstrained PG `numeric` do not; code ported between them silently acquires the failure |

**The Bitcoin 2010 shape: the sum wrapped before the comparison ran.** 2010-08-15, block **74638**: a
transaction created **184,467,440,737.09551616 BTC** across three outputs, exactly the 2⁶⁴-satoshi wrap
point. The wiki states the cause: "The code used for checking transactions before including them in a block
didn't account for the case of outputs so large that they overflowed when summed." The overflow happened
**inside the validation expression**, so `Σ outputs ≤ Σ inputs` compared the *wrapped* sum and passed. Fixed
by a soft-fork rule rejecting output-value overflow and any output > 21M BTC; patched client within five
hours; the honest chain overtook at block **74691** on 2010-08-16. CVE-2010-5139
(<https://en.bitcoin.it/wiki/Value_overflow_incident>).

The same shape, eight years later on a different stack: BEC/`batchOverflow` (CVE-2018-10299, exploited
2018-04-22 03:28:52 UTC). `batchTransfer(address[] _receivers, uint256 _value)` computed
`uint256 amount = cnt * _value` with no SafeMath; with `cnt = 2` and `_value = 2²⁵⁵`, `amount` wrapped to
**0**, so `balances[msg.sender] >= amount` passed while each recipient was credited 2²⁵⁵ tokens.

The rule that generalises both: **the guard must be evaluated on a value that cannot have wrapped.** Check
the accumulation, not the total.

```rust
// WRONG: the fold wraps in release builds; the comparison then validates the wrapped value.
let total: u64 = outputs.iter().map(|o| o.value).sum();
if total <= input_total { accept(); }

// RIGHT: the overflow is a failure of the check itself, not an input to it.
let total = outputs.iter().try_fold(0u64, |acc, o| acc.checked_add(o.value))
    .ok_or(Error::ValueOverflow)?;
if total <= input_total { accept(); }
```

Concretely: `checked_add`/`checked_mul` on every add and multiply in a money path on a bounded type; never
`unchecked { }` around value math in Solidity; `a * b / c` computed with a full-precision `mulDiv` carrying
an explicit rounding argument, because the intermediate `a*b` can exceed 2²⁵⁶ even when the result fits
(phantom overflow). In Python and Java the integers are unbounded, but the **column** is not, which is the
reason to declare `p` in `NUMERIC(p,s)` rather than leaving it bare.

---

## Currency as a type; FX as an explicit operation

Every stored amount carries its currency/asset identifier in the same row, struct or type, and every
comparison, sum and equality check reads it. The failure prevented is not exotic: `total += line.amount`
across a mixed-currency order, `SUM(amount)` with no `GROUP BY currency`, a USD refund issued against an EUR
charge, an FX result still labelled with the source currency. TigerBeetle makes it structurally
unrepresentable: the `ledger` field partitions accounts by asset and only accounts on the same ledger
transact directly, so a cross-currency transfer must be modelled as **atomically linked transfers** on two
ledgers, never as one transfer with a conversion inside.

How much a type system buys, per language (the honest version):

| Language | Mechanism | What it actually guarantees |
|---|---|---|
| F# | `[<Measure>] type USD` | true compile-time units; `usd + eur` does not compile, erased at runtime, free |
| Rust | newtype + `PhantomData<C>`, restricted operator impls | compile-time; `Money<USD> + Money<EUR>` does not typecheck |
| TypeScript | branded types | compile-time only and **fully erased**; brand a `bigint` or a Dinero object, never a `number`, or you have typed a float |
| Java / C# | JSR-354 `MonetaryAmount` throws on mismatched `CurrencyUnit` | runtime |
| Go | struct with an unexported field, constructor-only creation, runtime currency check | runtime; this is the pragmatic ceiling |
| Python | `py-moneyed` raises on cross-currency ops; `NewType` for mypy | runtime; the static hint does not survive `Decimal` arithmetic |

**Runtime enforcement that raises is worth more than compile-time enforcement that is easy to cast away.**
The non-negotiable is that the currency travels inside the value.

Conversion is therefore an operation with provenance, not a multiplication. The record persisted with the
resulting posting is `{from, to, rate, rate_scale, rate_source, rate_timestamp, rate_side}`:

```python
@dataclass(frozen=True)
class FxConversion:
    from_ccy: str; to_ccy: str
    rate: Decimal            # constructed from str; never 1/other_rate
    rate_scale: int          # significant figures or decimals, declared, not inferred
    rate_source: str         # the feed/venue that published it
    rate_timestamp: datetime # tz-aware UTC; a re-run must reproduce the number
    rate_side: Literal["bid", "ask", "mid"]
```

Three failure modes this closes, each a one-line diff:

- **No `rate_timestamp`** ⇒ re-running the job produces a different number and reconciliation is impossible.
- **`1 / rate` for the reverse leg** ⇒ that is not the reverse rate, and it re-rounds at a different scale.
- **`mid` on a customer-facing trade** ⇒ half the spread given away on every conversion, systematically,
  one direction. Bid for the client selling, ask for the client buying.

The one place this arithmetic is codified as law shows every component. The European Commission's restatement
of Council Regulation (EC) No 1103/97 Arts. 4–5: "The conversion rate from national currency to the euro is
expressed with **6 significant figures** – not to be confused with 6 decimal points"; "When conversions are
made, it is **prohibited to round or truncate the conversion rate**"; "Bilateral conversion rates between
national currency units are not defined and **cannot be used** since this may lead to inaccuracies"; "To
convert from one national currency into another, the national currency **must first be converted into euro**.
The resulting amount will be **rounded to at least three decimals** and then converted into the other
national currency"; and monetary amounts round **half-up**: "€1.264 becomes €1.26 … €1.265 becomes €1.27"
(<https://economy-finance.ec.europa.eu/euro/enlargement-euro-area/adoption-fixed-euro-conversion-rate/converting-euro_en>).
Mandated precision, mandated direction, mandated path, mandated intermediate precision, mandated final mode.
Generalise the shape, not the numbers: a conversion has a pivot, an intermediate precision, and exactly one
final rounding.

---

## The four tests this reference owes

1. **Round-trip across every serialization boundary**, with adversarial values: `0.1`, `1.005`, `8.475`, a
   value above 2⁵³, an 18-decimal token amount, a negative, and `-0`. `parse(serialize(x)) == x` exactly,
   and the *type* on the way back is the exact type, not merely a numerically equal one.
2. **The driver/ORM type assertion**, per money column, as written above. Float money rarely enters through
   *arithmetic* any more; it enters through a column type: freqtrade still declares every money field as SQL
   `Float` (`trade_model.py:95-117`).
3. **The width bound**, as an executable assertion rather than a comment:
   `assert max_expected_amount * 10**scale < TYPE_MAX` next to the type declaration, plus a
   `checked_*`-returns-`None` case in the overflow path.
4. **Cross-currency arithmetic raises.** `Money("10.00", "USD") + Money("10.00", "EUR")` must fail (at
   compile time where the language allows it, at runtime everywhere else), and the test asserts the
   exception, not a comment saying it would.

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
