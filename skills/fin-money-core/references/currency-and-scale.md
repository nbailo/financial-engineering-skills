# Currency and scale

Where the scale comes from, and why an integer amount with no `(currency, scale)` pair beside it is
uninterpretable. One currency at one vendor carries four possibly-different scales, all runtime metadata,
and a conversion between two currencies is an operation with provenance rather than a multiplication. Read
this before you write `* 100`, add a currency, or book an FX leg.

## Contents

- [ISO 4217 exponents, and the currencies that are not 2](#iso-4217-exponents-and-the-currencies-that-are-not-2)
- [Four scales for one currency at one vendor](#four-scales-for-one-currency-at-one-vendor)
- [Currency as a type; FX as an explicit operation](#currency-as-a-type-fx-as-an-explicit-operation)
- [The test this reference owes](#the-test-this-reference-owes)

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

## The test this reference owes

1. **Cross-currency arithmetic raises.** `Money("10.00", "USD") + Money("10.00", "EUR")` must fail (at
   compile time where the language allows it, at runtime everywhere else), and the test asserts the
   exception, not a comment saying it would.
