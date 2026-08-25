# Fees, funding and the events you did not instruct

The fee asset decides what a fee changes, the round trip is grossed up before it is quantized, and the
components that move a position without any order of yours (funding, ADL, liquidation, settlement, delivery)
are required parts of the total, each deduped by the venue's own settlement id rather than by a trade id.

## Contents

- **Commissions**: base-asset fees reducing credited quantity, third-asset conversion, `fees_unconverted`
- **The round-trip gross-up**, then **funding**: an accrual deduped by settlement id, the cliff, the schema
- **Orders you did not send**: ADL, liquidation, settlement, delivery, and their markers

## Commissions: the fee asset decides what the fee changes

| Fee asset relative to the instrument | Changes position size? | Changes realized PnL directly? | What to write |
|---|---|---|---|
| **Base** asset (spot BUY on Binance) | **Yes**: you received less | No | subtract from credited quantity, then re-snap down to `stepSize` in `Decimal` |
| **Quote / settlement** asset (spot SELL) | No | Yes | deduct in `net_realized_pnl` |
| **Third** asset (BNB discount) | No | Only after a conversion | convert at a **recorded rate with a named source and timestamp**, or surface `fees_unconverted` |
| **Null / absent** on the payload | Unknown | Unknown | not zero: re-read the trade record; the fee may arrive later |

Binance's commission FAQ is explicit that the fee currency flips with the side: **SELL → fee on the notional
(quote); BUY → fee on the `quantity` (base)**. The BNB discount applies only when
`discount.enabledForAccount && enabledForSymbol`, converts *standard* commission to BNB and multiplies by the
discount, and "the discount **does not apply to tax commissions or special commissions**". One `fee_rate`
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
trade records, and a fee whose rate could not be computed must not share a branch with a fee that is merely
small (freqtrade `freqtradebot.py:2548` accepts `fee_rate is None` under a comment saying it rejects >2%).

## The round-trip gross-up, and the quantization direction

Take the entry from the executed VWAP (`cummulativeQuoteQty / executedQty`), gross up by the full round trip at
the account's *effective* rates, and quantize **last**, away from the target:
`exit = vwap * (1+target) * (1+fee_in) * (1+fee_out)`, then `.quantize(tick, ROUND_UP)` for a sell target and
`ROUND_DOWN` for a buy. Entry 100.00, target +1%, 0.1% taker both sides, tick 0.01:
`100 × 1.01 × 1.001 × 1.001 = 101.2021…` → **101.21**, not 101.20. `ROUND_HALF_UP` surrenders up to half a tick
of the markup half the time and the error always points one way: costs understated, profit overstated.

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
first-class source, paginated until it returns fewer rows than the page size: a count at the documented cap is
a hole, not an empty page; both paths dedupe on `income_id`, so backfill and stream converge rather than
double. **Funding is a cliff, not a rate.** Hyperliquid pays **every hour**, capped at **4%/hour**, computed as
`position_size × oracle_price × funding_rate`, and is *"purely peer-to-peer and no fees are collected on the
payments"*. On a longer interval a position held 7h59m *inside* it pays zero, and one held for a single second
*across* the settlement timestamp pays the full interval. Funding cost is **not proportional to holding time**;
a model that amortises it pro rata will not reconcile to the venue's cash. Interval lengths are venue-specific:
read your venue's docs; the widely repeated 8-hour figure does not hold on every venue and must never be
hardcoded. Peer-to-peer funding also gives a free invariant wherever you observe the whole book: **Σ funding
across all accounts for an interval = 0**.

## Orders you did not send: ADL, liquidation, settlement, delivery

Filtering "orders that are mine" by "orders whose client ID I generated" excludes exactly the events that
change PnL without your consent: "mine" is defined by the **account**, not by ID authorship.

| Venue | Marker for a venue-generated order |
|---|---|
| Binance futures | client-ID prefixes `autoclose-` (liquidation), `adl_autoclose`, `settlement_autoclose-`, `delivery_autoclose-` |
| Bybit | an **empty** `orderLinkId` |

Both come from NautilusTrader's adapters rather than from a published venue reference page; **verify them
against your venue's current docs before keying on a prefix**, and make the unrecognised case loud. The
structural answer is to represent orders you did not originate: nautilus emits an external-order event, assigns
strategy id `EXTERNAL` and tag `VENUE`, and lets a strategy opt in via `external_order_claims`. Knight had the
right structure: the "33 Account", *"temporarily held … positions resulting from executions that Knight
received back from the markets that its systems could not match"* (34-70694 ¶23), wired to no control.
