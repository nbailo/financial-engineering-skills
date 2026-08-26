# Binance instrument metadata and filters

> **Provenance**
> provider: Binance · surface: spot and USDⓈ-M `exchangeInfo` instrument metadata, the symbol and exchange filter sets, and the futures-only precision fields
> version: as stated in this file's own header, the spot documentation repository at "Last Updated: 2026-07-27" and the derivatives documentation read 2026-08-24. Neither dating was re-checked here.
> verified_at: not established
> sources: https://github.com/binance/binance-spot-api-docs · https://developers.binance.com/docs/binance-spot-api-docs · https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it, including the dating in the header above. This file predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: `exchangeInfo` adds a filter type or a `symbolStatus` value; `MARKET_LOT_SIZE`, `NOTIONAL` or `PERCENT_PRICE_BY_SIDE` changes its field set or its predicate; the futures notional floor moves off the value quoted here; Binance moves the spot documentation off its public repository.

The filter set and its exact predicates, which order type each one validates, the metadata refresh
discipline behind them, and the futures-only fields that are not what their names suggest.

## Contents

- [Instrument metadata](#instrument-metadata): `exchangeInfo`, `symbolStatus`, refresh, fixture capture
- [Filter reference](#filter-reference): every filter, its predicate, and which order type it validates
- [Futures-only metadata](#futures-only-metadata): `pricePrecision` is not `tickSize`; COIN-M multipliers

## Instrument metadata

`GET /api/v3/exchangeInfo` / `GET /fapi/v1/exchangeInfo`. Fetch at startup, refresh on a schedule, and
**fail closed** (refuse to size) when a symbol's metadata is absent or older than a configured max age.
`symbolStatus` is a live field and gained a new value `CANCEL_ONLY` in 2026-07 (spot `CHANGELOG.md`): a symbol
can move to a state where `submit_order` is rejected while `cancel_all` still works, so branch on it. Tick,
step and notional minimums are revised and symbols are delisted, so anything cached at process start is a
stale snapshot, not a constant. Commit a **production-captured** fixture (`exchangeInfo.BTCUSDT.json`) for the
filter property test; hand-written fixtures agree with the hand-written rounder and prove nothing.

## Filter reference

From `filters.md` (spot docs repo). Filters live at two levels, `symbols[].filters[]` and top-level
`exchangeFilters[]`; any of `minPrice` / `maxPrice` / `tickSize` set to `0` disables **that clause only**.

| Filter | Fields | Predicate | Applies to |
|---|---|---|---|
| `PRICE_FILTER` | `minPrice`, `maxPrice`, `tickSize` | `price >= minPrice`, `price <= maxPrice`, `price % tickSize == 0` | any order carrying a price |
| `PERCENT_PRICE_BY_SIDE` | `bidMultiplierUp/Down`, `askMultiplierUp/Down`, `avgPriceMins` | BUY uses the **bid** multipliers, SELL the **ask** multipliers, against the `avgPriceMins` average | priced orders |
| `LOT_SIZE` | `minQty`, `maxQty`, `stepSize` | `minQty <= qty <= maxQty`, `qty % stepSize == 0` | **non-MARKET** orders |
| `MARKET_LOT_SIZE` | `minQty`, `maxQty`, `stepSize` (**its own values**) | same predicate, different numbers | **MARKET orders only** |
| `MIN_NOTIONAL` | `minNotional`, `applyToMarket`, `avgPriceMins` | `price * qty >= minNotional` | symbols exposing this variant |
| `NOTIONAL` | `minNotional`, `maxNotional`, `applyMinToMarket`, `applyMaxToMarket`, `avgPriceMins` | both bounds | symbols exposing this variant |
| `ICEBERG_PARTS` | `limit` | `ceil(qty / icebergQty) <= limit` | iceberg orders |
| `MAX_POSITION` | `maxPosition` | free base **+ locked base + qty of all open BUY orders** ≤ `maxPosition` | account-level, per symbol |
| `MAX_NUM_ORDERS` | `maxNumOrders` | counts **algo orders too** | per symbol |
| `MAX_NUM_ALGO_ORDERS` / `MAX_NUM_ICEBERG_ORDERS` / `MAX_NUM_ORDER_LISTS` / `MAX_NUM_ORDER_AMENDS` / `TRAILING_DELTA` | matching `maxNum*`; trailing min/max deltas | independent counters and bounds | per symbol / per order |
| `EXCHANGE_MAX_NUM_ORDERS` / `..._ALGO_ORDERS` / `..._ICEBERG_ORDERS` / `..._ORDER_LISTS` | n/a | account-wide across **all** symbols | `exchangeFilters[]` |

Four things this table exists to stop:

1. **`MARKET_LOT_SIZE` is a separate filter from `LOT_SIZE`, applying only to MARKET orders**, and its
   `maxQty` is frequently far below `LOT_SIZE.maxQty`. One shared `round_to_step(qty, market.lot_size.step)`
   helper produces `-1013 Filter failure: MARKET_LOT_SIZE` at the moment you are trying to exit fast.
2. **`MIN_NOTIONAL` and `NOTIONAL` are different filter types**; a symbol exposes one or the other, so code
   that only looks up `MIN_NOTIONAL` under-validates and never sees `maxNotional`.
3. **A MARKET order's notional is not computed from your price**: the engine substitutes the `avgPriceMins`
   VWAP, or last price when `avgPriceMins == 0`, so a client check against last price can pass while the
   engine rejects, and vice versa.
4. **`PERCENT_PRICE_BY_SIDE` is asymmetric.** The doc's own worked example is a bid band of 0.2–1.2 against
   an ask band of 0.8–5, so a symmetric `abs(price/ref - 1) < band` check is wrong on every symbol.

Do it all in `Decimal`: `0.29 % 0.01 == 0.009999999999999974` and `int(0.29/0.01) == 28` (CPython 3, verified);
float floor-division loses a whole step, and `str(1e-05) == '1e-05'` reaches the wire as a value the
decimal parser rejects (`-1100` or `-1111`; which fires was not established by live test).

## Futures-only metadata

**`pricePrecision` is not `tickSize`.** The USDⓈ-M `exchangeInfo` doc says verbatim of `pricePrecision`:
*"please do not use it as tickSize"*. Decimal count and tick size are independent constraints: a symbol can
carry `pricePrecision: 2` with `tickSize: 0.05`, and rounding to two decimals produces `100.03`, which is not
a multiple of the tick. Read `tickSize` from the symbol's `PRICE_FILTER` and `stepSize` from its `LOT_SIZE`,
on futures exactly as on spot. The notional floor is `-4164 "Order's notional must be no smaller than 5.0
(unless you choose reduce only)"`; the number is not universally 5, so read it from the symbol's filters, and
a residual close below it must set `reduceOnly` or refuse to send.

**COIN-M quantity is in contracts, not base asset.** BTCUSD contracts are 100 USD each; most alt COIN-M
contracts are 10 USD (Binance contract-specification support page). `size = usd_notional / price` on COIN-M is
off by 100×, while USDⓈ-M linear quantity is in base asset. Same exchange, two unit systems; carry the unit
with the number across every module boundary and convert only in the adapter.
