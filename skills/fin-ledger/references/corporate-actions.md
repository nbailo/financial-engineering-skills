# Corporate actions and supply events

The family of events that changes quantity, basis or balance with nobody trading: the ex, record and pay date
triple after T+1, splits that move quantity *and* average cost, cash-in-lieu, ticker reuse, migrations, rebases
and funding cliffs.

**The date triple, post-T+1.** FINRA Rule 11140(b), verbatim: for a distribution of **less than 25 %** of the value of
the subject security, *"the date designated as the 'ex-dividend date' shall be **the record date if the record date
falls on a business day**, or the **first business day preceding the record date**"* if it is not. The T+1 compliance
date was **28 May 2024** (SEC Release 34-96930). Under T+2 the ordinary ex-date was one business day *before* the
record date, so **any `ex_date = record_date.minus_business_days(1)` written before mid-2024 is now off by one day**,
and one day is the entire entitlement question. For distributions of **25 % or more** the ordering inverts: *"the
ex-dividend date shall be the **first business day following the payable date**."* Large special dividends, stock
dividends and splits effected as distributions land here, so `assert ex_date <= record_date <= pay_date` rejects
valid events.

**The announced rate is provisional by design.** Rule 10b-17(b)(1)(v)(a) permits *"a **reasonable approximation** of
the per share distribution … so long as the **actual per share distribution is subsequently provided on the record
date**"*, and guarantees notice only *"no later than **10 days prior to the record date**"*. A corporate-action record
therefore has a lifecycle (announced → revised → final), stored as versioned rows keyed `(issuer_event_id, version)`,
the entitlement job reading the version in force at its own business date.

**A split moves quantity and average cost in one transaction.** For an `a:b` split, `qty ← qty × a/b` and `avg_cost ←
avg_cost × b/a`, so `qty × avg_cost` is invariant and unrealised PnL is preserved; realised PnL already booked is
**not** restated. If quantity is updated by one job and basis by another, every read between them is wrong by `a/b`:
for a 1:10 reverse split, a **10×** error in unrealised PnL, in the direction of a spurious gain. The
regulator-verified consequence is the Robinhood AWC (FINRA No. 2020066971201, 30 June 2021): the performance chart
*"either overstated customers' gains or understated customers' losses … including because Robinhood mistakenly did not
properly account for … cash and position movements caused by corporate actions"*, with *"**no internal system …
triggered any alerts**"*. Apply the same event to open orders, price history and every derived series, not only to
positions.

**Fractional shares in a reverse split are a disposal, not a rounding.** SEC investor education: *"In some reverse
stock splits, small shareholders are '**cashed out**' (receiving a proportionate amount of cash in lieu of partial
shares)."* Cash-in-lieu creates a cash movement, realises PnL against the disposed fraction's basis and reduces the
remaining basis; `new_qty = round(old_qty / 10)` conserves neither shares nor money; rounding up mints shares from
nothing, rounding down destroys them silently.

**Ticker symbols are not identity.** A symbol can be changed by its issuer and later reassigned by the exchange to an
unrelated company, splicing two issuers' prices, actions and positions together. Key on a permanent internal
instrument id and model the symbol as a time-bounded attribute (`symbol, valid_from, valid_to`). *(Mechanism; no
citable rule located.)* Delisting likewise kills the price feed, not the position: **a missing price is not a zero
price**.

**Crypto analogues.** A migration or fork snapshot height is a **record date**; the crediting event is a **pay date**;
the moment the old asset stops trading with the entitlement attached is an **ex-date**. A redenomination (1 : 1000) is
a split and takes the quantity-and-basis rule unchanged. An airdrop is a stock dividend with **zero cost basis**;
booking it at market value as income *and* at market value as basis double-counts.

**Rebasing tokens break `balanceOf` assumptions.** Lido, verbatim: *"stETH token balances get recalculated **daily**
when the Lido oracle reports the Consensus Layer ether balance update"*, and the integration rule: *"it's highly
recommended to **store and operate shares** rather than stETH public balances directly, because stETH balances change
both upon transfers, mints/burns, **and rebases**, while shares balances can only change upon transfers and
mints/burns."* A rebase is an accrual delivered as a balance mutation: a position keeper diffing `balanceOf` books a
phantom deposit; an accounting system must attribute it to a period as income. The event stream is not the ledger here.
`ScaledBalanceTokenBase._burnScaled`, verbatim: *"**In some instances, a burn transaction will emit a mint event if
the amount to burn is less than the interest that the user accrued**"*; and the `Transfer` event emits *"the input
amount"* while `BalanceTransfer` emits *"the precise scaled amount"*. **Σ `Transfer` values will never reconcile to
balance changes for a scaled or rebasing token**; reconcile on scaled-balance-and-index, or on `balanceOf` deltas.

**Perpetual funding is a cliff, not a continuous accrual.** Hyperliquid, verbatim: *"The funding rate on Hyperliquid
is paid **every hour**"*, *"Funding is **purely peer-to-peer and no fees are collected on the payments**"*, capped
*"at 4%/hour"*, with payment `position_size * oracle_price * funding_rate` using the **oracle** price. On a venue with
an 8-hourly schedule a position held 7 h 59 m inside an interval pays **zero**, and one held one second across the
funding timestamp pays the **full** interval; funding cost is not proportional to holding time, and a pro-rata
amortisation never matches the venue's cash. *(The 8-hourly schedule is the industry norm; any specific venue's
schedule is unverified here.)* Where funding is peer-to-peer you get a free continuous assertion: **Σ funding payments
across all accounts for an interval == 0**.
