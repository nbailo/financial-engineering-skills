# Position, PnL, fees and funding

How to derive a position and a profit number that survives comparison with the venue's own. Average price is a
fold over the persisted fill set, not an accumulator; realized quantity is capped on a flip; contract
multipliers and inverse/quanto payoffs change the unit before they change the number; and the components that
move a position without any order of yours — funding, ADL, liquidation, settlement, delivery — are required
parts of the total, each deduped by the venue's own settlement id. This file also carries the corporate-action
adjustments that preserve PnL across a split.

## Contents

- **The fold** — average entry as an order-independent fold, why incremental diverges, the arrival-order test
- **Position keys** — `(symbol, positionSide)`, netting vs hedge, the fabricated flat −3; **adds, reduces and
  flips** — `avg_px_open` vs `avg_px_close`, realized quantity capped, the closed-cycle snapshot
- **Contract units** — per-venue units, `ctMult × ctVal`, Deribit USD amounts, the 1/x payoff, quanto
- **Mark vs index vs last** — which price for which decision; the stop-distance mismatch
- **Commissions** — base-asset fees reducing credited quantity, third-asset conversion, `fees_unconverted`
- **The round-trip gross-up**, then **funding** — an accrual deduped by settlement id, the cliff, the schema
- **Orders you did not send** — ADL, liquidation, settlement, delivery, and their markers; **silent zero** —
  `Decimal(0)` unrealized PnL reported as "flat, no exposure"
- **Reconciliation** — what to compare, the join key, the tolerance, and gating on the result
- **Corporate actions** — splits that preserve PnL, cash-in-lieu, ex/record/pay dates, symbol identity
- **Margin** — isolated vs cross keying, snapshot-not-delta, liquidation distance

## The fold: average entry price is a pure function of the fill set

```python
def avg_entry_px(fills: Iterable[Fill], voided: Mapping[TradeId, Decimal]) -> Decimal | None:
    """Fold the SURVIVING fills. Order-independent. Returns None, never Decimal(0)."""
    notional = quantity = Decimal(0)
    for f in fills:                                  # the persisted set, in any arrival order
        effective = f.last_qty - voided.get(f.trade_id, Decimal(0))
        if effective == 0:
            continue
        notional += effective * f.last_px            # Decimal throughout; no float ever enters
        quantity += effective
    return None if quantity == 0 else notional / quantity
```

That is `avg_px_from_fills` in NautilusTrader (`crates/model/src/orders/mod.rs:1355-1382`), folding every
retained `OrderFilled` event in `Decimal` less any quantity an `OrderFillVoided` removed; its docstring
(`orders/mod.rs:25-28`) states the invariant: *"a rebuild agrees with the incremental update over the same
fills."* Freqtrade reached the same design with no shared code — `recalc_trade_from_orders`
(`freqtrade/persistence/trade_model.py:1265`) walks `self.orders` from scratch on every call. The incremental
`avg = (avg * filled + px * qty) / (filled + qty)` is not a faster equivalent; it is a different function, and
it is wrong under exactly the event shapes a real venue produces:

| Event a real stream produces | Incremental accumulator | Fold over the persisted set |
|---|---|---|
| Redelivered fill after a reconnect (same `trade_id`) | `filled` inflated, `avg` permanently pulled toward the duplicated price, not undoable without the fill list that was never kept | Idempotent — the duplicate carries the same `trade_id` and is already in the set |
| REST backfill interleaved with the live socket | Order-dependent; the corruption is **permanent** | Transient reordering only; the final value is identical |

**Ship the test.** Nautilus asserts exactly this: `test_avg_px_invariant_to_fill_arrival_order`
(`orders/mod.rs:1769`) feeds the same fills ascending and descending and asserts a byte-identical `avg_px`;
`test_avg_px_keeps_a_quotient_no_f64_can_hold` (`:1728`) asserts `dec!(1.6666666666666666666666666667)` is
**not equal** to `Decimal::from_f64_retain(5.0_f64 / 3.0)`.

```python
@given(fills=st.lists(fill_strategy(), min_size=1, max_size=20))
def test_avg_px_invariant_to_arrival_order(fills):
    a = avg_entry_px(fills, {})
    assert avg_entry_px(list(reversed(fills)), {}) == a   # Decimal equality, not pytest.approx
    assert avg_entry_px(fills + fills[:1], {}) == a       # a redelivered fill changes nothing
```


**The storage boundary is where the fold leaks** — it is only as exact as the column it reloads from. Freqtrade
declares every money field as SQL `Float` (`ft_amount`, `price`, `average`, `filled`, `cost`, `funding_fee`,
`ft_fee_base`, all `mapped_column(Float())`, `trade_model.py:95-117`) and then aggregates those floats in
`ccxt.Precise` string math (`ft_precise.py:9`). Declare `NUMERIC(38, 18)`; check the JSON, protobuf `double`
and CSV export paths too.

## Position keys: `(symbol, positionSide)`

Read the account's position mode at startup and key every position row, fill router and reconciliation join on
the pair. On Bybit and OKX the mode is **account-wide**: one read at session start, re-read after any account
change, because a mode flip changes the key space of every row you already persisted.

| | Netting (one-way) | Hedge (dual-side) |
|---|---|---|
| Rows per symbol | 1 | 2 — one per side |
| Key | `(symbol)` suffices | `(symbol, positionSide)` is **required** |
| A BUY against a short | reduces, then flips through flat | opens/adds the LONG row; SHORT is untouched |
| `reduceOnly` | available | **unavailable on Binance futures in hedge mode** |
| Flatten | one order | one order **per side** |

The failure is a dict keyed on the symbol alone, with last-write-wins:

```python
positions[(sym, side)] = qty     # LONG +5, SHORT -3  → 8 gross, +2 net
positions[sym] = qty             # LONG +5 then SHORT -3  → -3
```

`−3` is not a rounding error and it is not the conservative choice — it is a position that does not exist, and
everything downstream is computed from it: sizing reads `max_position − (−3)` and lets you buy 8 more;
`flatten()` sends a BUY 3 and leaves the real +5 and −3 both live; liquidation distance is measured from the
wrong side of the mark. This is a common defect in hand-rolled position keepers. Check reduce-only
preconditions against the mode *before* sending: unavailable in Binance hedge mode and on Bybit spot;
`closePosition` incompatible with `quantity`; Binance rejects a conflicting reduce-only with `-2022
ReduceOnly Order is rejected`; Bybit **splits** an oversized reduce-only order rather than rejecting it, and
`closeOnTrigger` can cancel or reduce *your other orders* to make room.

## Adds, reduces and the flip through flat

Three quantities; conflating any two redistributes money between realized and unrealized. `avg_px_open` moves
**only** on an add — a reduce must not touch it. `avg_px_close` is a *separate* weighted average whose base is
the closing quantity only (`calculate_avg_px_close_px` weights by `sell_qty` for a long, `buy_qty` for a short,
`position.rs`); using total position quantity as the base double-counts. `realized_pnl` accrues against
`avg_px_open` and **the fill price**, never against `avg_px_close`.

```python
def apply_fill(pos, side, qty: Decimal, px: Decimal):
    signed = qty if side is BUY else -qty
    same_direction = pos.signed_qty == 0 or (pos.signed_qty > 0) == (signed > 0)

    if same_direction:                                   # ADD — nothing is realized
        pos.opening_fills.append((qty, px))
        pos.avg_px_open = avg_entry_px(pos.opening_fills, pos.voided)
    else:                                                # REDUCE or FLIP
        closing = min(qty, abs(pos.signed_qty))          # ← the cap. Not `qty`.
        pos.realized_pnl += pnl_raw(pos.avg_px_open, px, closing, pos.side)
        pos.closing_fills.append((closing, px))
        pos.avg_px_close = avg_entry_px(pos.closing_fills, {})
        # pos.avg_px_open is deliberately NOT recomputed here
        if qty > abs(pos.signed_qty):                    # flipped through flat
            snapshot_closed_cycle(pos)                   # archive BEFORE the reset
            pos.opening_fills, pos.closing_fills = [(qty - abs(pos.signed_qty), px)], []
            pos.avg_px_open = px                         # reset to the FLIPPING fill's price
    pos.signed_qty += signed
```

Nautilus's cap is literally `let quantity = quantity.min(self.signed_qty.abs());` inside `calculate_pnl_raw`
(`crates/model/src/position.rs`). **Worked flip:** long 100 @ 50,000, one fill sells 150 @ 51,000.

| | Capped (correct) | Uncapped |
|---|---|---|
| Realized quantity | `min(150, 100) = 100` | 150 |
| Realized PnL | `100 × (51,000 − 50,000) = 100,000` | `150 × 1,000 = 150,000` |
| Resulting position | short 50, `avg_px_open = 51,000` | short 50, entry usually left at 50,000 |
| Unrealized at mark 51,000 | 0 | `50 × 1,000 = 50,000` of phantom PnL |

The uncapped version books 50,000 against units that were never open, and it is self-concealing: realized plus
unrealized often still looks plausible while both halves are wrong in opposite directions — the same signature
as recomputing `avg_price` on every fill regardless of direction, which drags "entry" on a partial close.
**Snapshot the closed cycle before the position reopens**: in netting mode the position object resets at flat,
so archive quantities, average prices, realized PnL, fill events and per-currency commission totals *first* and
sum snapshot PnL into the instrument total. Otherwise the reset destroys historical realized PnL — and the flip
case destroys it inside a single event.

## Contract units: linear, inverse, quanto, and the multiplier

Attach a unit to every size crossing a module boundary — base / quote / contracts / USD-notional — and convert
**only** in the venue adapter, from the venue's own multiplier field.

| Venue / class | Quantity field and unit | Multiplier source | Notional | PnL settles in |
|---|---|---|---|---|
| Spot anywhere, Binance USDⓈ-M | base units | 1 | `qty × price` | quote (USDT/USDC on USDⓈ-M) |
| Binance COIN-M | **contracts** | BTCUSD **100 USD**, most alts 10 USD | `qty × mult / price` | base coin |
| OKX derivatives | `sz` = **contracts** (spot/margin `sz` is base) | `ctMult × ctVal` | per class | per class |
| Deribit perp / inverse futures | `amount` is **USD** (`contracts` is the alternative; if both are sent they must agree) | `contract_size` | `amount / price` | base (BTC/ETH) |

Resolve the cost currency from the instrument's classification, never a global default: **base for inverse,
settlement for quanto, quote otherwise** (`crates/model/src/instruments/mod.rs`, `cost_currency`). Notional
follows the same split — `try_notional_value` is `qty × multiplier / price` for inverse, `qty × mult × price`
otherwise. **Inverse contracts are non-linear in price:** `points_inverse` is `1/entry − 1/exit` for a long and
`1/exit − 1/entry` for a short, and `pnl = qty × multiplier × points_inverse`, denominated in the **base**
currency. It is undefined without a base currency and for any price ≤ 0 or below `1e-15` — error, never
substitute. Worked, Binance COIN-M BTCUSD (multiplier 100 USD/contract), long **10 contracts** (1,000 USD
notional), 50,000 → 55,000:

```
1/50000 − 1/55000 = 0.000020000000000000 − 0.000018181818181818 = 0.000001818181818182
pnl = 10 × 100 × 0.000001818181818182 = 0.00181818181818 BTC          ( = $100 at 55,000)
```

Cross-check by hand: 1,000 USD costs 0.02 BTC at entry and 0.018181… BTC to buy back at exit. The linear
formula `(55,000 − 50,000) × 10 × 100` returns **5,000,000**, in the wrong unit, off by ~2.75 billion×; the
error is near-zero at the entry price and grows with the move, so a one-tick test passes. **Quanto**
instruments settle in a currency that is neither base nor quote: compute the payoff in the quote unit, then
convert at the venue's settlement rate — name that rate source and store it with the number, and never
substitute 1.0 for an unavailable FX rate (nautilus's portfolio explicitly refuses that fallback).

**The double-conversion failure** is a multiplier applied twice, or in neither place:

- `size_in_contracts = usd_notional / price` on Binance COIN-M ignores the 100 USD multiplier — off by 100×.
- On Deribit, `amount` for an inverse perp is *already* USD; dividing by price turns a correct value wrong.
- On OKX, applying `ctMult × ctVal` in both the adapter and your own sizing code squares it — as does applying
  the multiplier in `notional()` **and** again in `pnl()` inside one position object.

## Mark vs index vs last: which price for which decision

Three numbers, three jobs. Binance publishes them together on `GET /fapi/v1/premiumIndex`, which returns
`markPrice`, `indexPrice`, `estimatedSettlePrice`, `lastFundingRate`, `interestRate` and `nextFundingTime`.

| Decision | Correct price | Evidence |
|---|---|---|
| Unrealized PnL, account equity | **mark** | one print through a thin book moves last; mark does not |
| Maintenance margin, liquidation distance | **mark** | liquidation is a mark-price event |
| Stop / take-profit trigger | **the venue's trigger selector** | Binance `workingType` ∈ `MARK_PRICE` / `CONTRACT_PRICE`; Deribit `trigger` ∈ `index_price` / `mark_price` / `last_price` |
| Funding accrual | the venue's own reference | Hyperliquid computes `position_size × oracle_price × funding_rate` |
| Realized PnL | **neither** — the fill price | realized PnL is a fact about a trade, not a valuation |

**The stop-distance mismatch.** You compute a stop 2% below *last* and send it; the venue arms it against
*mark*, because that is what `workingType` selects. The distance you sized risk from is not the distance the
engine uses, and the two diverge exactly when it matters — during a wick. Two more Binance mechanisms sit on
that divergence: `priceProtect` blocks execution when mark and contract price diverge beyond a symbol
threshold, and `-4131` rejects a market order whose counterparty best price breaches `PERCENT_PRICE`. State in
code which price each calculation uses; a variable called `price` in a risk function is a defect. Each price is
stored with the **venue's own event timestamp** and a declared `max_age`, and the order path evaluates
`now − ts > max_age` before use — not `ts < last_seen`, which a dead feed still passes.

## Commissions: the fee asset decides what the fee changes

| Fee asset relative to the instrument | Changes position size? | Changes realized PnL directly? | What to write |
|---|---|---|---|
| **Base** asset (spot BUY on Binance) | **Yes** — you received less | No | subtract from credited quantity, then re-snap down to `stepSize` in `Decimal` |
| **Quote / settlement** asset (spot SELL) | No | Yes | deduct in `net_realized_pnl` |
| **Third** asset (BNB discount) | No | Only after a conversion | convert at a **recorded rate with a named source and timestamp**, or surface `fees_unconverted` |
| **Null / absent** on the payload | Unknown | Unknown | not zero — re-read the trade record; the fee may arrive later |

Binance's commission FAQ is explicit that the fee currency flips with the side: **SELL → fee on the notional
(quote); BUY → fee on the `quantity` (base)**. The BNB discount applies only when
`discount.enabledForAccount && enabledForSymbol`, converts *standard* commission to BNB and multiplies by the
discount — and "the discount **does not apply to tax commissions or special commissions**". One `fee_rate`
scalar is wrong three ways at once: wrong currency, wrong rate, wrong composition. **Worked**, buy 36.38 GTC
with a 0.1% fee taken in GTC:

```
fee = 36.38 × 0.001 = 0.03638 GTC;  credited = 36.34362 GTC  ← the position, not 36.38
sellable = floor_to_step(36.34362, step=0.01) = 36.34
```

Selling `trade.amount` (36.38) returns `-2010` insufficient balance; selling the raw free balance (36.34362)
returns `-1013 Filter failure: LOT_SIZE` because it is not a multiple of `stepSize`. Only the floored figure
works. Freqtrade encodes the first half as `safe_amount_after_fee = self.safe_filled - self.safe_fee_base`. The
same arithmetic on the close side is where "flat" stops being flat: *"Selling a 0.999 BTC LONG position with
0.000999 BTC commission leaves you SHORT 0.000999 BTC, not FLAT."* Over thousands of round trips that is drift
with a constant sign. **Third-asset fees are not optional either**: on a BNB-discount account, excluding BNB
commissions from `net_realized_pnl` overstates headline profit by the entire fee bill. Convert, or make the
omission impossible to ignore:

```python
@dataclass(frozen=True)
class RealizedPnl:
    amount: Decimal; currency: str
    fees_converted: dict[str, Decimal]        # asset -> amount already folded into `amount`
    conversion_rates: dict[str, ConvRate]     # asset -> (rate, source_name, as_of_ts)
    fees_unconverted: dict[str, Decimal]      # asset -> amount NOT in `amount`

if pnl.fees_unconverted:                      # in every consumer, not in a logger
    raise UnconvertedFees(pnl.fees_unconverted)
```

**A fee that reached you through an adapter may be a guess.** NautilusTrader's Binance adapter *estimates*
`default_taker_fee × qty × price` for USD-M linear when the commission is missing and defaults COIN-M inverse
commission to **zero**; CCXT on `calculateFee`: *"experimental, unstable… Do not rely on precalculated
values."* Tag the provenance (`venue_reported` vs `estimated`) and reconcile estimates against the venue's
trade records — and a fee whose rate could not be computed must not share a branch with a fee that is merely
small (freqtrade `freqtradebot.py:2548` accepts `fee_rate is None` under a comment saying it rejects >2%).

## The round-trip gross-up, and the quantization direction

Take the entry from the executed VWAP (`cummulativeQuoteQty / executedQty`), gross up by the full round trip at
the account's *effective* rates, and quantize **last**, away from the target:
`exit = vwap * (1+target) * (1+fee_in) * (1+fee_out)`, then `.quantize(tick, ROUND_UP)` for a sell target and
`ROUND_DOWN` for a buy. Entry 100.00, target +1%, 0.1% taker both sides, tick 0.01:
`100 × 1.01 × 1.001 × 1.001 = 101.2021…` → **101.21**, not 101.20. `ROUND_HALF_UP` surrenders up to half a tick
of the markup half the time and the error always points one way — costs understated, profit overstated.

## Funding is an accrual, deduped by settlement id

Funding is neither a fill nor a commission: it changes realized PnL with **no quantity change**, is never
folded into `avg_px_open`, and is retained as an auditable adjustment row.

| Economic event | Dedupe on | Changes quantity? | Changes realized PnL? |
|---|---|---|---|
| Fill | the venue's `trade_id` | yes | yes |
| Funding settlement | the venue's income / settlement id | **no** | yes |
| ADL, liquidation | `trade_id` (it arrives as a fill on a venue-generated order) | yes | yes |
| Settlement, delivery | the venue's settlement id | yes (goes flat) | yes |

`balance += funding` with no id double-counts on every redelivery and every backfill.
```sql
CREATE TABLE funding_accruals (              -- income_id is the VENUE's id, never one you minted
  venue text NOT NULL, account_id text NOT NULL, income_id text NOT NULL,
  symbol text NOT NULL, position_side text NOT NULL, asset text NOT NULL,
  amount numeric(38,18) NOT NULL,            -- NUMERIC: freqtrade declares funding_fee as Float()
  settled_at timestamptz NOT NULL, PRIMARY KEY (venue, account_id, income_id));
INSERT INTO funding_accruals (...) VALUES (...)   -- the stream and the backfill share this statement
  ON CONFLICT (venue, account_id, income_id) DO NOTHING;
```

**The order stream alone is not enough.** Ingest the venue's income / transaction-history endpoint as a
first-class source, paginated until it returns fewer rows than the page size — a count at the documented cap is
a hole, not an empty page; both paths dedupe on `income_id`, so backfill and stream converge rather than
double. **Funding is a cliff, not a rate.** Hyperliquid pays **every hour**, capped at **4%/hour**, computed as
`position_size × oracle_price × funding_rate`, and is *"purely peer-to-peer and no fees are collected on the
payments"*. On a longer interval a position held 7h59m *inside* it pays zero, and one held for a single second
*across* the settlement timestamp pays the full interval. Funding cost is **not proportional to holding time**;
a model that amortises it pro rata will not reconcile to the venue's cash. Interval lengths are venue-specific
— read your venue's docs; the widely repeated 8-hour figure does not hold on every venue and must never be
hardcoded. Peer-to-peer funding also gives a free invariant wherever you observe the whole book: **Σ funding
across all accounts for an interval = 0**.

## Orders you did not send: ADL, liquidation, settlement, delivery

Filtering "orders that are mine" by "orders whose client ID I generated" excludes exactly the events that
change PnL without your consent: "mine" is defined by the **account**, not by ID authorship.

| Venue | Marker for a venue-generated order |
|---|---|
| Binance futures | client-ID prefixes `autoclose-` (liquidation), `adl_autoclose`, `settlement_autoclose-`, `delivery_autoclose-` |
| Bybit | an **empty** `orderLinkId` |

Both come from NautilusTrader's adapters rather than from a published venue reference page — **verify them
against your venue's current docs before keying on a prefix**, and make the unrecognised case loud. The
structural answer is to represent orders you did not originate: nautilus emits an external-order event, assigns
strategy id `EXTERNAL` and tag `VENUE`, and lets a strategy opt in via `external_order_claims`. Knight had the
right structure — the "33 Account", *"temporarily held … positions resulting from executions that Knight
received back from the markets that its systems could not match"* (34-70694 ¶23) — wired to no control.

## Silent zero is an economic claim

```python
def unrealized_pnl(self) -> Decimal:
    if self.mark_px is None:
        return Decimal(0)          # ← reports "flat, no exposure" on a live open position
```

Returning zero for an unavailable price is a common defect, and it appears in both `unrealized_pnl()` and
`notional()`; `except ValueError: unrealized = Decimal(0)` inside `snapshot()` reintroduces the same lie one
layer up after the leaf is fixed. `Decimal(0)` is not "we don't know" — it is a number a risk consumer acts on.
The regulator-verified form is FCA Final Notice, Citigroup Global Markets Ltd, 17 May 2024 ¶4.27: an
unavailable index price **defaulted to −1**, the pre-trade estimate computed `quantity × −1`, rendered
**−58,000,000**, and the trader read the number they expected and clicked Execute. The same missing feed
blanked the wave-notional soft block — *"Due to lack of market data, Wave notional cannot be found"* (¶4.30) —
and the order proceeded anyway.

The correct shape is an explicit absent type plus a documented, ordered fallback that reports its own quality:
mark → side-appropriate quote (**BID for longs, ASK for shorts**) → last trade → most recent bar close. If none
is current, **carry** the last valid price but set `is_stale` and name the instrument in `stale_instruments`;
if no price has **ever** existed, the position belongs in `unpriced_instruments` and is **excluded from the
sum**, not contributed as zero. FX never falls back to 1.0, and `create_inferred_fill` returns `None` rather
than substituting 0 in the fill path. Direction matters in the assertions: **prices may be negative** (CME
crude, 2020-04-20); **quantities may not** — "a price is not non-negative" produces `assert price >= 0`.

## Reconciling against the venue's own numbers

| Local quantity | Venue authority | Join key | Tolerance |
|---|---|---|---|
| Signed position size | position report / position endpoint | `(symbol, positionSide)` | one lot at the instrument's `size_precision` |
| Realized PnL | the venue's realized figure on the execution event, plus income history | `(symbol, positionSide, period)` | the instrument's tick × quantity, never a fixed epsilon |
| Funding paid | income / transaction history | `income_id` | exact |
| Fee bill per asset | trade records | `trade_id` | exact |

Where the venue publishes its realized-PnL figure **on the execution event**, cross-check on every event, not
only on the schedule; receiving that field and never comparing it is the step most integrations skip. Choose
the cadence to **exceed the venue's documented replication lag**: Binance labels each endpoint's data source
(Matching Engine / Memory / Database) and warns *"the API system is asynchronous, so some delay in the response
is normal and expected."* The private stream is ME-sourced and many REST reads are not, so a reconciliation
running faster than the lag oscillates and gets muted. Express the tolerance in the instrument's own units. Nautilus carries both forms —
`const DEFAULT_TOLERANCE: Decimal = Decimal::from_parts(1, 0, 0, false, 4); // 0.0001`
(`crates/execution/src/reconciliation/positions.rs:40`) and a single-unit tolerance keyed on the instrument's
size precision (`is_within_single_unit_tolerance`, `positions.rs:423`). **And then it gates.** The worked
negative example comes from a mature production platform:

```rust
// crates/execution/src/engine/mod.rs:1737
let _ = check_position_reconciliation(report, cached_signed_qty, size_precision);
```

`check_position_reconciliation` returns `bool`; its failure path is a `log::warn!` naming `cached` and `venue`
and nothing else (`positions.rs:410-445`), while the module docstring (`:19-21`) calls it *"the core invariant
maintained here"*. Nautilus **does** gate at startup — the trader is not started unless reconciliation succeeded
(`crates/live/src/node/mod.rs:440`) — and that asymmetry is the point. On mismatch, record the venue's value,
close the risk gate for that instrument, and reopen only on a successful reconcile.

## Corporate actions: splits, cash-in-lieu, dates, identity

**A split moves quantity and average cost in one transaction, or PnL is wrong in the window between.** For an
`a:b` split, `qty ← qty × a/b` and `avg_cost ← avg_cost × b/a`, leaving `qty × avg_cost` — and therefore
unrealized PnL — invariant. Split across two jobs, any read between them sees PnL wrong by `a/b`: a 1:10
reverse split gives a **10× error, in the direction of a spurious gain**. Realized PnL already booked is **not**
restated. **Cash-in-lieu is a disposal, not a rounding**: the SEC states that in some reverse splits small
shareholders are *"cashed out (receiving a proportionate amount of cash in lieu of partial shares)"* — a cash
movement realizing PnL against the disposed fraction's basis, so `round(old_qty / 10)` conserves neither shares
nor money.

**Dates.** Post-T+1 (compliance 28 May 2024, SEC Release 34-96930), FINRA Rule 11140(b) sets the ordinary
ex-dividend date **equal to** the record date — *"the record date if the record date falls on a business day"* —
so any `ex_date = record_date.minus_business_days(1)` written under T+2 is off by one day, and one day is the
entire entitlement question. For distributions of **25% or greater** the ordering inverts (*"the first business
day following the payable date"*), so `assert ex_date <= record_date <= pay_date` rejects valid events. Rates
may be provisional: Rule 10b-17(b)(1)(v)(a) permits *"a reasonable approximation"* if the actual is supplied on
the record date. **Symbol is not identity** — a ticker can be reassigned to an unrelated company, splicing two
issuers' prices, actions and positions together; key on a permanent instrument id with the symbol as a
time-bounded attribute *(mechanism; no citable rule text located)*. And an **unadjusted historical series**
shows a discontinuity where no trade occurred — a 10:1 forward split looks like a −90% gap — so every stop and
per-share cost comparison crossing it reads a move that never happened. The Robinhood AWC is the documented
consequence, its footnote 15 recording securities *"incorrectly returned as having zero value for purposes of
mark-to-market valuations"* — the same silent zero, in the valuation path.

## Margin: isolated vs cross, and liquidation distance

**Isolated and cross are different keying schemes.** A margin balance keyed by `instrument_id` is isolated; one
keyed by currency with `instrument_id = None` is cross. Nautilus's `MarginAccount.apply()` **replaces** both
stores from the incoming event rather than merging, which forces the adapter contract: every live margin entry
must appear on every update. It fails durably in either direction — merging a full snapshot leaves stale
isolated entries for closed positions inflating used margin forever; replacing on a partial update drops live
entries. **Reduce-only orders must not reserve margin or lock cash**: they do not contribute to
`balance_locked` on cash accounts, nor to initial margin on margin accounts.

**Liquidation distance** is computed from the **mark** price against maintenance margin, and its scope follows
the margin mode: under cross margin a loss on an unrelated instrument moves this instrument's liquidation
price, so a per-symbol distance computed in isolation is the wrong number. Maintenance-margin rates are tiered
by notional on the major perp venues and the tables are venue-specific and change — fetch them from the venue's
margin-tier endpoint at startup and cache with a refresh, as you do `exchangeInfo`. **No bracket table is
reproduced here: any table printed in a document goes stale, and a stale hardcoded tier is a liquidation you
did not predict.** Checking **in-flight reserved margin** before sizing is the step most integrations skip: an
order in `INFLIGHT_UNKNOWN` consumes margin at the venue while your sizing code counts it as zero.
