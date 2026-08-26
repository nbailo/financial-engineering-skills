# OKX, Bybit and Kraken order books

> **Provenance**
> provider: OKX, Bybit and Kraken · surface: the three book-integrity models: OKX `seqId` and `prevSeqId` after the checksum retirement, Bybit's restart signal and cross-depth comparator, and Kraken's CRC32 over the top ten levels
> version: as stated in this file's own header, all venue text read 2026-08-24. No documentation version is published by any of the three.
> verified_at: not established
> sources: https://www.okx.com/en-sg/help/okx-order-book-channels-checksum-field-deprecation · https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook · https://docs.kraken.com/api/docs/websocket-v2/book · https://docs.kraken.com/api/docs/guides/spot-ws-book-v2/
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it. This file predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The checksum arithmetic written out for Kraken is reproducible against a live frame and that is the cheapest way to recheck it; nobody did so for the 2026-08-25 review pass. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: OKX restores a meaningful checksum, or changes `seqId` and `prevSeqId` semantics or the affected channel list; Bybit changes the restart signal or what `seq` compares; Kraken changes the checksum input, the level count or the string formatting it is computed over.

Three book-integrity models that share no algorithm and no failure mode: OKX's `seqId`/`prevSeqId` after its
checksum was retired to a constant `0`, Bybit's `u == 1` restart signal and the separate `seq` that compares
across depths, and Kraken's CRC32 over exactly the top ten levels in their string forms. All venue text was
read on **2026-08-24**; re-verify before keying production behaviour on any of it.

## Contents

- OKX book: `seqId`/`prevSeqId`, and the checksum that is always 0 from 2026-06-23
- Bybit book: `u == 1` is a restart, `seq` is the cross-depth comparator
- Kraken book: the CRC32 checksum written out, with reproducible arithmetic

## OKX book: `seqId`/`prevSeqId`, and the checksum that is now always 0

> "the checksum field will remain present in push messages but **will always return 0 and must no longer be
> used for data integrity validation**"
> Source: <https://www.okx.com/en-sg/help/okx-order-book-channels-checksum-field-deprecation>

Affected channels: `books`, `books-l2-tbt`, `books50-l2-tbt`. Demo **2026-06-02**, production **2026-06-23**.
`books5` and `bbo-tbt` are unaffected; `books-rpi` never carried one. Integrity moves to `seqId`/`prevSeqId`.

Both obvious guard shapes fail on the changeover day, in opposite directions:

```python
if computed_crc != msg["checksum"]: resync()                        # WRONG: resyncs on every frame
if msg.get("checksum") and computed_crc != msg["checksum"]: resync()  # WRONG: 0 is falsy, stops validating

cs = msg.get("checksum")                                            # RIGHT: the field's loss is an event
if cs is None or cs == 0:
    raise IntegrityFieldRetired("OKX checksum is 0; seqId/prevSeqId is the only integrity signal")
```

The sequence rule, as implemented by an operator: `prevSeqId` must equal the `seqId` you last applied. On
mismatch, **drop the frame and suppress all further updates until a fresh snapshot arrives with
`prevSeqId: -1`**; do not patch, do not interpolate, do not fetch the missing levels.

```python
def on_books_update(self, msg):
    prev, cur = msg["prevSeqId"], msg["seqId"]
    if prev == -1:                                  # snapshot
        self.book.replace(msg); self.applied = cur; self.suppressed = False; return
    if self.suppressed: return                      # stay dark until the next prevSeqId == -1
    if prev != self.applied:
        self.suppressed = True
        self.book.invalidate()                      # gates order submission, not just a log line
        self.request_snapshot(); return
    self.book.apply(msg); self.applied = cur
```

**Unresolved:** whether `seqId` can legitimately decrease; secondary sources describe resets, and OKX's
order-book channel page is a JS-rendered SPA that was not readable in the verification pass. The rule above is
safe under either answer: it never trusts a frame whose linkage it cannot verify.

## Bybit book: `u == 1` is a restart, `seq` is the comparator

From <https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook>:

- `type` is `snapshot` or `delta`.
- **`u == 1` means the service restarted** and the message is a fresh snapshot that must **overwrite** the
  local book. It is not update number 1 in a sequence you can continue.
- For `orderbook.1`, a snapshot is re-emitted with the **same `u`** if nothing changed for 3 seconds. A
  repeated `u` there is normal, not a gap.
- **`seq` is a separate cross-depth comparator**: "for the smaller `seq`, the data is generated earlier". It is
  how you order an `orderbook.1` message against an `orderbook.50` message for the same instrument. `u` cannot.

Gap detection built solely on "`u` strictly increases" does both wrong things at once: false resyncs on the
level-1 same-`u` re-send, and a missed restart signal.

```python
def on_depth(self, msg):
    u, seq = msg["data"]["u"], msg["data"]["seq"]
    if msg["type"] == "snapshot" or u == 1:
        self.book.replace(msg["data"]); self.u = u; self.seq = seq; return
    if self.u is None or u != self.u + 1:
        self.book.invalidate(); self.resubscribe(); return   # discard, never patch
    self.book.apply(msg["data"]); self.u, self.seq = u, seq
```

## Kraken: the CRC32 book checksum, written out

Two facts that both have to hold (<https://docs.kraken.com/api/docs/websocket-v2/book>,
<https://docs.kraken.com/api/docs/guides/spot-ws-book-v2/>):

1. **The checksum covers exactly the top 10 levels per side, regardless of the depth you subscribed to**: 10,
   25, 100, 500 or 1000. Checksum a 100-deep book and you mismatch on every message.
2. **It is computed over the *string* forms.** The guide instructs: *"Parse `price` and `qty` fields using a
   decimal or string decoder to preserve full precision."* Kraken v2 sends these as JSON **numbers**; a default
   parser turns `0.10000000` into float `0.1`, the trailing zeros vanish, and the checksum never matches again.

```
for each of the top 10 ASKS, ASCENDING by price:   emit price_str, then qty_str
for each of the top 10 BIDS, DESCENDING by price:  emit price_str, then qty_str
  where each token = the string with "." removed, then leading "0"s removed
concatenate in that order, encode UTF-8, CRC32, cast to UNSIGNED 32-bit, compare to `checksum`
```

Reproducible in a REPL (three levels a side for brevity; slice at 10 in production):

```python
import json, zlib
from decimal import Decimal

raw = ('{"channel":"book","type":"snapshot","data":[{"symbol":"BTC/USD",'
       '"bids":[{"price":45283.5,"qty":0.10000000},{"price":45283.4,"qty":1.20000000},'
                '{"price":45283.0,"qty":0.00500000}],'
       '"asks":[{"price":45284.1,"qty":0.05000000},{"price":45284.5,"qty":2.00000000},'
                '{"price":45290.0,"qty":0.30000000}]}]}')

d = json.loads(raw, parse_float=Decimal)["data"][0]   # Decimal keeps "0.10000000" intact
tok = lambda v: str(v).replace(".", "").lstrip("0")

parts = []
for lvl in d["asks"][:10]:                            # asks ascending
    parts += [tok(lvl["price"]), tok(lvl["qty"])]
for lvl in d["bids"][:10]:                            # bids descending
    parts += [tok(lvl["price"]), tok(lvl["qty"])]

payload = "".join(parts)
# '45284150000004528452000000004529003000000045283510000000452834120000000452830500000'
assert zlib.crc32(payload.encode("utf-8")) & 0xFFFFFFFF == 2158453468
```

Run the identical code with a plain `json.loads(raw)` and the payload collapses to
`'45284154528452045290034528351452834124528305'`, CRC32 `1275150685`: **same book, unrelated integer, on the
first message.** Not a subtle drift you debug later.

- **Sort numerically, not lexically**: `"45283.5"` sorts before `"9000.0"` as a string. **A level whose `qty`
  is 0 is a delete:** remove it before checksumming, since `"0".lstrip("0")` is empty and shortens the payload.
- **You checksum your reconstructed book**, using the last `qty` string received per resting level; retain the
  string form of every level, not only the top 10.
- On mismatch: **discard the book and re-subscribe.** The checksum says the book is wrong, not which level.
