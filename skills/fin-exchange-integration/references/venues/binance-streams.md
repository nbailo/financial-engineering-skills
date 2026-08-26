# Binance user data stream, execution reports and commission

> **Provenance**
> provider: Binance · surface: the user data stream lifecycle on spot and USDⓈ-M, the execution report's cumulative and last-fill fields, and the commission rules that decide which asset a fee comes out of
> version: as stated in this file's own header, the spot documentation repository at "Last Updated: 2026-07-27" and the derivatives documentation read 2026-08-24. Neither dating was re-checked here.
> verified_at: not established
> sources: https://github.com/binance/binance-spot-api-docs · https://developers.binance.com/docs/binance-spot-api-docs · https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it, including the dating in the header above and the deprecation dates quoted for the spot `listenKey` path. This file predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The third-party citations, the NautilusTrader adapter paths and the two freqtrade issues, were not opened either. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: the spot `listenKey` path is removed, or the Ed25519 requirement for the WebSocket user data stream changes; an execution report field is added or renamed; the BNB discount scope, or the asset a commission is charged in, changes.

The user-data stream lifecycle and the two migrations under it, the cumulative-versus-last-fill split in the
execution payload, and the commission rules that decide which asset a fee is taken out of.
Facts are dated to the docs revision read (spot repo HEAD "Last Updated: 2026-07-27", derivatives 2026-08-24),
so re-verify before keying production behaviour on one.

## Contents

- [User data stream](#user-data-stream): `listenKey`, `listenKeyExpired`, the Ed25519 migration
- [`executionReport`: cumulative vs last-fill](#executionreport-cumulative-vs-last-fill)
- [Commission](#commission): the side flip and the BNB discount's scope

## User data stream

**USDⓈ-M futures.** `POST /fapi/v1/listenKey` starts a stream; the key *"will close after 60 minutes unless a
keepalive is sent"*; `PUT /fapi/v1/listenKey` extends it by 60 minutes. Critically: *"if the account has an
active `listenKey`, that `listenKey` will be returned and its validity will be extended for 60 minutes"*, so
`POST`ing again does **not** rotate the key, and code that "gets a fresh key" on reconnect is a keepalive
under a different name.

**Spot.** Receiving user data on `wss://stream.binance.com:9443` via `listenKey` was **deprecated 2025-04-07**
and all `listenKey` documentation for that endpoint was **removed 2025-10-24** (`CHANGELOG.md`). The
replacement is subscribing to the User Data Stream on the **WebSocket API**, which **requires an Ed25519 API
key**: an HMAC key cannot subscribe, so a spot bot authenticating with an HMAC secret and calling
`POST /api/v3/userDataStream` is on a path Binance has said will be removed.

Both venues: run the keepalive on a scheduler strategy work **cannot starve**, renewing at ≤30 min against the
60-min TTL: a keepalive `await`ed in the same event loop as a blocking indicator misses one tick, the stream
closes, and the bot keeps trading against its last known position. Treat `listenKeyExpired` as **"you are now
blind"** (halt new orders, reconcile, resync), not an informational log line. And **subscribe and confirm the
private stream before sending the first order**: placing first loses the initial `NEW`/`TRADE` events and the
order starts life untracked.

## `executionReport`: cumulative vs last-fill

Spot `executionReport` carries both, for different jobs.

| Field | Meaning | Use it for |
|---|---|---|
| `z` | **cumulative** filled quantity | position: `filled = z` (assignment, not `+=`) |
| `Z` | **cumulative** quote asset transacted | notional |
| `l` / `L` / `Y` | **last** executed quantity / price / quote quantity (`L * l`) | the fill record |
| `n` / `N` | commission amount / **commission asset** | fee booking; `N` can be **null** on non-trade events |
| `t` | trade id | the fill dedupe key |
| `i` / `c` / `C` | orderId / clientOrderId / original clientOrderId | correlation |
| `X` / `x` / `T` | order status / execution type / transaction time | state machine, ordering, staleness |

The doc states the derivation outright: **"Average price can be found by doing `Z` divided by `z`."**
`position += l` is **not** idempotent under reconnection, replay, or dual stream+poll ingestion; `filled = z`
is. Build the fill record from `l`/`L`/`t` and the order status from `z`/`Z`, exactly the split
NautilusTrader's Binance futures adapter makes
(`crates/adapters/binance/src/futures/websocket/streams/parse_exec.rs`), and dedupe fills on `t` before the
position transition, persisting the dedupe set in the same transaction as the position row. USDⓈ-M emits
`ORDER_TRADE_UPDATE` with the order fields **nested under `o`** (`o.z`, `o.l`, `o.L`, `o.t`, …); verify the
full futures field map against the current derivatives doc before keying on a field this table does not name,
because several widely-circulated futures field names are not in any primary source.

## Commission

**The fee side flips with the order side** (`faqs/commission_faq.md`): on a **SELL** the commission is charged
on the notional, i.e. in the **quote** asset; on a **BUY**, *"the received amount would be `quantity`"*; the
commission comes out of the **base asset you just bought**. So buying 36.38 GTC with a 0.1% GTC-denominated
fee leaves you holding **36.34** GTC. Selling `trade.amount`
returns `-2010 "Account has insufficient balance for requested action."` (freqtrade#1371); selling the raw
free balance returns `-1013 Filter failure: LOT_SIZE` because 36.34 is not a multiple of `stepSize`
(freqtrade#5481). The correct model, in `Decimal`: `credited = filled_qty − (fee if commissionAsset == base)`,
then re-snap **down** to `stepSize`.

**The BNB discount's scope is narrower than a single `fee_rate` scalar can express.** When
`discount.enabledForAccount && discount.enabledForSymbol`, the **standard** commission is converted to BNB and
multiplied by `discount`, and the doc states the discount *"does not apply to tax commissions or special
commissions"*, so one scalar rate is wrong three ways at once: wrong currency, wrong rate, wrong composition.
Book the fee in the asset the venue reports (`commissionAsset` / `N`), handle null, and where it is neither
the quote nor the settlement asset, convert at a **recorded** rate or surface it as unconverted.
Do not treat an adapter's fee as truth: NautilusTrader's Binance adapter *estimates*
`default_taker_fee × qty × price` for USD-M linear when Binance omits the commission and defaults COIN-M
inverse commission to zero; ccxt's `calculateFee` is *"experimental, unstable and may produce incorrect
results"*.
