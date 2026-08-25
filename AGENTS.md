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

Load every row that matches. A crypto exchange backend that credits deposits is `fin-onchain` **and**
`fin-ledger`.
