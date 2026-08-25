# Financial correctness routing

When a task changes code that trades, moves, stores, settles, prices, reserves, or accounts for
economically valuable assets, consult the most relevant installed `fin-*` skill before implementation
or review. The skill carries the rules, the evidence and the output format; this file only points at it.

Do not mark a financial risk resolved merely because it is described. Implement the control, make the
unsafe path unreachable, or state explicitly that the risk remains unresolved.

## Which skill

| The code | Load |
|---|---|
| sends, cancels or tracks orders on a venue it does not operate, or derives fills, positions, PnL or a book from one | `fin-exchange-integration` |
| integrates a payment processor or rail: intents, capture, refunds, disputes, webhooks, payouts | `fin-payments` |
| maintains balances, postings, holds, or double-entry books | `fin-ledger` |
| crosses the chain boundary: transactions, nonces, finality, reorgs, indexing, token semantics | `fin-onchain` |
| none of the above, or amount arithmetic, retry logic, or rollout of a money path | `fin-money-core` |
| is about to ship, or needs tests, reconciliation, or proof it is correct | `fin-verification` |

## How many

A domain skill normally wins alone. `fin-money-core` loads alongside it only for a cross-domain mechanism
that skill does not cover, never merely because a domain-specific retry appears. `fin-verification` loads
for tests, proof, reconciliation, review or readiness, or where a domain skill demands stronger proof;
customer money alone is not a trigger. Otherwise load every row that matches: a backend crediting deposits
is `fin-onchain` **and** `fin-ledger`.

Matching, allocation and market-data publication are venue-side and opt-in, outside the installed set.
Clearing, liquidation waterfalls and venue-operated resolution have no skill here; say so.
