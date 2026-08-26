# Hyperliquid: `cloid` correlates, `(signer, nonce)` guards the replay

> **Provenance**
> provider: Hyperliquid · surface: order identity and recovery, the nonce window, the significant-figure price rule, and order statuses
> version: as stated in this file's own body, the 2026-08-24 research pass. No API version was recorded.
> verified_at: not established
> sources: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it. This file's material predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The body already carries its own list of what the original research did not establish, and that list is still the right one to read before asserting anything from here; this block adds only that nothing on the rest of the file has been re-read since. The URL above is where a recheck starts; it resolved on 2026-08-25, and nothing in it was read against a claim in this file.
> revalidate_when: Hyperliquid documents a dedup guarantee for `cloid`, changes the nonce window or its time bounds, changes the significant-figure price rule, or publishes a rate-limit shape.

Hyperliquid's `cloid` is a correlation key with no documented dedup guarantee. The real replay guard is `(signer, nonce)`,
so re-signing a retry converts it into a duplicate order while resending the byte-identical signed action under the
identical nonce does not. Price validity here is significant figures rather than a tick size, which a generic rounding
helper gets wrong. Facts are as of the 2026-08-24 research pass.

## Contents

- The identity model, and where it sits against a venue with idempotent replay
- `cloid` as correlation only; the `(signer, nonce)` window: 100 highest nonces per signer, bounded by T−2d…T+1d
- The byte-identical resend, and the one line that breaks it
- The ≤5-significant-figure / ≤`MAX_DECIMALS − szDecimals` price rule, with quantizer and worked arithmetic
- Order statuses, and `iocCancelRejected` as a rejection rather than an expiry
- Recovery endpoints and time bounds
- What the research does not establish and must not be asserted

---

## The identity model

| | Hyperliquid |
|---|---|
| Field | `cloid` (optional 128-bit hex `0x…32 hex`) |
| Length / charset | exactly 32 hex digits after `0x` |
| Venue-enforced uniqueness | **none documented** |
| Behaviour on reuse | undocumented: assume a second order |
| Class | C: no collision check |
| Query by it? | `info` → `orderStatus` accepts `oid` **or** `cloid` |
| The real replay guard | **`(signer, nonce)`**, not `cloid` |
| Safe ambiguous-submit action | resend the **byte-identical signed action under the identical nonce** |

One venue in this suite sits in class A, idempotent replay, where a duplicate client order id returns the original order
and the re-POST is the supported query. That guarantee is on the client identifier there and on the nonce here, so the
branch does not carry across.

Source: Hyperliquid's exchange-endpoint and nonces-and-api-wallets pages under
<https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/>.

---

## Hyperliquid

### `cloid` is a correlation key; the nonce is the replay guard

The entire published specification of `cloid` is one sentence: "**Client Order ID (cloid) is an optional
128 bit hex string**, e.g. 0x1234567890abcdef1234567890abcdef". No uniqueness statement, no collision
behaviour, no window. It is a cancel/modify selector (`cancelByCloid`;
`modify` accepts `Number | Cloid`) and an `orderStatus` lookup key, and nothing more. NautilusTrader uses
`cloid` to dedupe *inbound status reports*, a strictly weaker claim than create-order dedup. **Whether a
reused `cloid` is rejected or silently creates a second order is undocumented; assume the latter.**

The actual idempotency mechanism lives at the signature layer:

> "On Hyperliquid, **the 100 highest nonces are stored per address. Every new transaction must have nonce
> larger than the smallest nonce in this set and also never have been used before.** Nonces are tracked per
> signer, which is the user address if signed with private key of the address, or the agent address if signed
> with an API wallet. **Nonces must be within (T - 2 days, T + 1 day)**, where T is the unix millisecond
> timestamp on the block of the transaction."

> "Once an agent is deregistered, its used nonce state may be pruned … **previously signed actions can be
> replayed once the nonce set is pruned.**"

Read the window carefully: it is **not a duration**. It is *"until 100 further nonces have been consumed by
that signer"*, additionally bounded by T−2d…T+1d. For a maker batching every 100 ms that is under ten seconds,
and under one second if each batch consumes several; the replay guarantee can expire between your timeout and
your retry.

### The safe retry, and the one line that breaks it

```python
# Persisted BEFORE the socket write: the nonce, the exact signed action bytes, and the signature.
intent = {
    "cloid":     "0x" + uuid4().hex,          # correlation only; never relied on for dedup
    "nonce":     nonce,                       # ms timestamp, strictly increasing per signer
    "action":    action,                      # the exact dict that was signed
    "signature": sig,                         # signing may be non-deterministic: you cannot rebuild this
}
db.commit(intent)                             # fsync before send

def retry_hyperliquid(http, intent):
    # SAFE: byte-identical resend. Rejected as a used nonce if the first attempt landed; accepted if not.
    return http.post("/exchange", json={"action":    intent["action"],
                                        "nonce":     intent["nonce"],      # IDENTICAL
                                        "signature": intent["signature"]}) # IDENTICAL

def retry_hyperliquid_WRONG(http, intent, wallet):
    nonce = int(time.time() * 1000)           # ← this line converts a safe retry into a second order
    return http.post("/exchange", json=sign(intent["action"], nonce, wallet))
```

The wrong version is what every "just refresh the timestamp and re-sign" helper does, and it is
indistinguishable from the right version in a review that greps for `cloid`. Two operational consequences. **Do not reuse API-wallet (agent) addresses**: once an agent is deregistered
its nonce state may be pruned and previously signed actions become replayable; generate a new agent wallet per
deployment. **The nonce is per signer**: two processes sharing one agent wallet share one 100-nonce set and
evict each other's retry windows, so run one signer per order-emitting process.

Recovery query: `info` → `orderStatus` accepts `oid` **or** `cloid`, but the authoritative retry primitive is
the nonce, not the query. The query says what happened; the nonce says what a resend will do.

### Price validity is significant figures, not tick size

Two constraints apply **simultaneously**: **≤ 5 significant figures** and **≤ `MAX_DECIMALS − szDecimals`
decimal places**, with `MAX_DECIMALS` = **6** (perps) / **8** (spot), and one exemption overrides the first:
"**Integer prices are always allowed, regardless of the number of significant figures**". Sizes are rounded to
`szDecimals`.

```python
from decimal import Decimal, ROUND_DOWN, ROUND_UP

def hl_price(px: Decimal, sz_decimals: int, is_spot: bool, side: str) -> str:
    """Quantize toward validity. Buy rounds down, sell rounds up; never to nearest, which can make
    the order more aggressive than the strategy intended."""
    mode = ROUND_DOWN if side == "BUY" else ROUND_UP
    if px == px.to_integral_value():                 # integer prices skip the sig-fig rule entirely
        return str(int(px))
    max_dec = (8 if is_spot else 6) - sz_decimals
    five_sig = px.quantize(Decimal(1).scaleb(px.adjusted() - 4), rounding=mode)   # 5 significant figures
    out = five_sig.quantize(Decimal(1).scaleb(-max_dec), rounding=mode)           # then the decimal cap
    if out <= 0:                                     # a formatter must be total: valid price, or raise
        raise ValueError(f"quantized to {out} from {px}")
    return format(out.normalize(), "f")
```

Worked, on a perp with `szDecimals = 2` ⇒ `max_dec = 6 − 2 = 4`:

| Input price | 5 sig figs | ≤4 decimals | Sent | Why |
|---|---|---|---|---|
| `0.0312345` | `0.031234` | `0.0312` | `0.0312` | the decimal cap binds, not the sig-fig cap |
| `104237.5` | `104240` | `104240` | `104240` | sig figs bind; result is an integer, which is always legal |
| `1.234567` | `1.2346` | `1.2346` | `1.2346` | both caps satisfied by the sig-fig step |
| `12.3` | `12.3` | `12.3` | `12.3` | already legal: quantization must be idempotent |

**Do not reuse a tick-size rounder here.** ccxt#23516 is the measured failure: `decimal_to_precision` called
with `counting_mode=exchange.precisionMode` (`TICK_SIZE`) against a precision value that was an integer decimal
count (`5`) **silently returned `0`** for a price of `0.18119111`. A zero price on a live order is an instant
rejection or a fill at an absurd level. Assert the output is `> 0` and re-parses legal before it reaches the
signer.

### Order statuses

`resting` (with `oid`), `filled` (carries `totalSz`, `avgPx`, `oid`), `error`. TIF is `Alo` (post-only),
`Ioc`, `Gtc`; `r` is reduce-only; `grouping` is `na` / `normalTpsl` / `positionTpsl`. **An IOC that finds no
match is a *rejection*, not an expiry**: it reports `iocCancelRejected`, which NautilusTrader surfaces as
`OrderRejected`, so a state machine mapping "IOC with no fill" to `EXPIRED` never sees it. Post-only that would
cross is likewise **rejected**, not repriced: the opposite of Deribit.


## Recovery endpoints and time bounds

| Venue | Endpoint | Accepts the client identifier? | Bound |
|---|---|---|---|
| Hyperliquid | `info` → `orderStatus` | by `cloid` or `oid` | the resend under the identical nonce is the stronger primitive |

---

## Not established by the research: do not assert

- **Hyperliquid rate limits** (address-weighted or otherwise). No primary source captured; build the limiter from the
  live documentation, not from this file.
- **Hyperliquid `l2Book` / feed sequencing.** Not sourced. Do not assume another venue's per-product sequence number or
  book-revision id model transfers here.
- **Hyperliquid `cloid` collision behaviour.** Undocumented. Assume a reused value creates a second order.
