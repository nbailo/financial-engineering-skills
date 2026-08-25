# Multi-currency and FX on one journal

Currency as a dimension of the entry rather than a shape of the schema: the asset scale that travels with it,
functional versus transaction currency, the two FX accounts that are not the same account, and the ban on
mixed-currency arithmetic.

**One journal with a currency dimension**: not one table or one database per currency, which are schemas
people spend a year unwinding. The dimension carries currency *and scale* and is immutable: TigerBeetle's
`ledger` (u32, non-zero) partitions accounts by asset and its asset scale maps the smallest useful fractional
unit to 1 (USD→2, JPY→0, KWD→3); "asset scales cannot be changed after account creation without migrating to a
new ledger". Formance encodes scale into the asset identifier itself: `USD/2`. An integer amount without its
`(currency, scale)` pair is meaningless.

**Functional versus transaction currency.** IAS 21 defines the **functional currency** as that of the primary
economic environment in which the entity operates, and allows a different **presentation currency** for
reporting; the *transaction currency* is the one the deal was struck in and is what goes on the leg. Initial
recognition is at the spot rate on the transaction date. At each reporting date **monetary items** (cash,
receivables, payables, customer balances) are remeasured at the closing rate while **non-monetary items** stay
at their historical rate, with the exchange differences generally going to profit or loss.

**FX gain/loss accounts: two of them, and they are not the same account.**
`revenue:fx_spread:{pair}` is the margin you charged, booked as its own posting: TigerBeetle's
currency-exchange recipe requires the spread be a *separate* transfer rather than baked into the rate, because
rate-plus-spread merged into one number "cannot be derived" afterwards. `income/expense:fx_revaluation:{ccy}`
is the difference between the historical rate a monetary balance was booked at and the closing rate. Selinger's
currency-trading account is the clean construction: an account holding a multi-currency expression such as
`USD 100 − CAD 120` makes unrealised gain/loss fall out of revaluation with no adjusting entries, and realised
gain is recognised on disposal: "the purpose of a currency trading account is not to perform conversions, but
to calculate gains and losses."

**The ban.** No expression adds, subtracts or compares two amounts of different currencies, and no rate is
multiplied in place onto a stored balance. `SUM(amount_minor)` without `GROUP BY currency` yields 2000 of
nothing when it sees 1000 JPY and 1000 USD, and it does not raise. An FX movement is a *balanced transaction
per currency* (source-currency legs netting to zero, target-currency legs netting to zero, joined through
liquidity/trading accounts) with rate, provenance, side and pivot on the transaction, and the conversion
residue posted to a named residue account.
