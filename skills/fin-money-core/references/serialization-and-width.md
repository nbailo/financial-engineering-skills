# Serialization boundaries and integer width

The six boundaries at which a correct in-memory value is silently degraded on its way to storage or to a
counterparty, and the width past which the arithmetic stops holding the number you think it holds. The
arithmetic is rarely where exactness fails; these are. Read this before you declare the protobuf message, the
Avro schema, or the integer type.

## Contents

- [Serialization boundaries where precision dies](#serialization-boundaries-where-precision-dies)
- [Integer width and overflow](#integer-width-and-overflow)
- [The two tests this reference owes](#the-two-tests-this-reference-owes)

---

## Serialization boundaries where precision dies

The arithmetic is rarely where this fails. These six are.

| Boundary | Mechanism | What the value becomes | Correct shape |
|---|---|---|---|
| **JSON number** | RFC 8259 §6: "good interoperability can be achieved by implementations that expect no more precision or range than IEEE 754 binary64 … integers in the range [-(2\*\*53)+1, (2\*\*53)-1] are interoperable". Damage happens **inside the parser** | `JSON.parse('{"amount":0.1}')` yields the double `0.1000000000000000055511…`; `JSON.parse("12345678901234567890")` yields `12345678901234567000` | amounts as **strings** or integer minor units. `Decimal(str(json_number))` is *not* a fix: `str()` of an already-damaged double is already wrong |
| **ORM / driver coercion** | a correct `NUMERIC(18,8)` handed back as `float64` / JS `Number` | scale and exactness gone after a correct write | assert the returned **type** in a test |
| **protobuf `double`** | identical to binary64 | same as JSON | `google.type.Money` `{currency_code, units:int64, nanos:int32}`, a string field, or `{scaled_int, scale}`. Note `Money`'s fixed 10⁻⁹ scale **cannot** hold an 18-decimal token amount |
| **Avro / Parquet `decimal`** | `precision` and `scale` live in the **schema**; the payload is only the two's-complement big-endian unscaled integer | a schema evolution changing `scale` from 2 to 4 reinterprets every historical record as 1/100 of its true value, with no error | treat `scale` as immutable once data exists |
| **CSV / Excel** | Excel stores 15 significant decimal digits and zeroes everything beyond (<https://learn.microsoft.com/en-us/troubleshoot/microsoft-365-apps/excel/floating-point-arithmetic-inaccurate-result>); CSV carries no type at all | a 19-digit amount or a leading-zero account number becomes a float on open | never route money through a spreadsheet inside an automated pipeline |
| **Float SQL column** | `REAL` / `DOUBLE PRECISION` / `FLOAT` rounds on INSERT | correct in memory, wrong at rest | an exact decimal column with an explicit scale, never a float type |

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

## The two tests this reference owes

1. **Round-trip across every serialization boundary**, with adversarial values: `0.1`, `1.005`, `8.475`, a
   value above 2⁵³, an 18-decimal token amount, a negative, and `-0`. `parse(serialize(x)) == x` exactly,
   and the *type* on the way back is the exact type, not merely a numerically equal one.
2. **The width bound**, as an executable assertion rather than a comment:
   `assert max_expected_amount * 10**scale < TYPE_MAX` next to the type declaration, plus a
   `checked_*`-returns-`None` case in the overflow path.
