# Changelog

Notable changes to the skill suite. The unit of change here is a rule: what an agent is told, and whether it
is true. Rewordings are not listed; corrections always are.

## v0.5.0

### Corrected

- **Polymarket guidance rewritten against production CLOB V2.** The previous material described V1: a signed
  order carrying user-supplied `feeRateBps`, `nonce` and `taker`, and USDC.e as collateral. All three fields
  were removed from the V2 signed struct, `timestamp`, `metadata` and `builder` were added, pUSD replaced
  USDC.e, and fees are now selected by the protocol at match time rather than embedded in the order. Standard
  and Neg Risk markets differ by verifying contract, not by collateral asset. `tickSizeTtlMs` is gone.
- **Flat-sell reserve arithmetic.** The reserve for selling a YES share you do not own is `(1 - p) - p`,
  which is `1 - 2p`. The suite stated `2p - 1`, the negation, which is wrong for every price below 0.50.
- **Flat-sell semantics partitioned by venue.** Kalshi can model a flat-account YES sell through
  complementary NO exposure; Polymarket requires the seller to own the tokens being sold. Neither is
  universal, and the suite previously stated one as though it were.
- **Matching-engine exposure.** Working-order exposure, filled-position exposure and settlement exposure are
  three quantities. A fill atomically reduces working leaves and changes position exposure; the claim that
  fills never decrement order exposure was wrong.
- **Recovery under changed logic.** Replaying commands through current matching logic lets a code change
  silently reinterpret historical executions. Authoritative recovery now requires either immutable persisted
  decisions, or replay pinned to the reducer version, configuration and build identity that originally
  produced them. Replay under changed logic is shadow analysis, not recovery.
- **Self-trade prevention** is the venue's published mode, tested as chosen, rather than a hard-coded
  reject-or-cancel-newest policy.
- **Market-data publication**: twelve rules that were true of one protocol or one jurisdiction and stated as
  universal properties of feeds are now qualified to the rulebook that says them. The crossed-book assertion
  is the clearest case: `bid <= ask` holds for a continuous executable book whose rulebook forbids a crossed
  or locked state, and not during an auction.
- An invariant citation in `fin-market-data-publication` named a `fin-money-core` rule that does not exist.
- **FIX order cancel reject.** The literal `NONE` on an unknown order belongs to `OrderID(37)`, not to
  `OrigClOrdID(41)`, and the same message sets `OrdStatus` to Rejected. A client keying its lookup where the
  suite said to key it would have searched for the wrong field.
- **`OrderQty = CumQty + LeavesQty` is not unconditional.** The FIX 4.4 dictionary names exceptions when
  `ExecType` or `OrdStatus` is Canceled, DoneForTheDay, Expired, Calculated or Rejected. Asserting the
  identity on every report, as the suite said to, turns ordinary terminal reports into false breaks.
- **ccxt citations.** Five file and line anchors were off by one against the pinned commit and were moved to
  the lines that hold the code. No KuCoin implementation carries `createMarketBuyOrderRequiresPrice` at that
  commit, and the two further fee warnings in the manual are separate warnings, not repetitions of the
  `calculateFee` one.
- **Regulation NMS.** One finding was quoted in a shape the SEC order does not use, and the sentence the file
  opens with is the Commission's description of the rule rather than the codified text, which says only that
  data is distributed on terms that are fair and reasonable and not unreasonably discriminatory. Both are now
  stated as what they are.

### Added

- Six prediction-market references under `fin-exchange-integration`: cross-venue core, client-side settlement
  integration, Polymarket V2, Kalshi, Limitless and Hyperliquid outcome markets. Prediction markets remain a
  specialisation of the exchange skill, not a seventh installed skill.
- `examples/prediction-market-bot/`, the fifth worked example.
- A provider-support matrix with four honest levels: deep and verified, generic, experimental, unverified.
- Provenance blocks on provider and protocol references: provider, surface, version, `verified_at`, source
  URLs, pinned commits, verified claims, **explicitly unverified claims**, and a revalidation trigger.
- `SECURITY.md`, issue templates for an incorrect invariant, provider drift and a new incident, and a pull
  request template that asks for sources, unverified claims, routing impact and token-budget impact.
- Provenance blocks on the last eighteen provider and protocol references, which clears the validator's
  known-debt list. Three were re-checked against their sources on the day and carry a real `verified_at`: the
  ccxt reference against a pinned commit, the FIX reference against the FIX 4.4 dictionary and Binance's own
  FIX document, and the Regulation NMS file against the SEC order and the current CFR text. The other fifteen
  carry `verified_at: not established`, which `scripts/validate.py` now accepts as an explicit non-answer and
  reports on its own line, because a file written from real sources by an earlier pass and re-read by nobody
  since should say so rather than show a date nobody earned.

### Changed

- Routing: a domain skill normally wins alone. `fin-money-core` no longer loads merely because a
  domain-specific retry exists, and `fin-verification` loads for tests, proof, reconciliation or a readiness
  question rather than because customer money is involved.

## v0.4.0

- Rules that turned one reasonable implementation into a universal prescription were weakened or deleted.
  The unresolved-order policy stopped prescribing cancel-then-flatten on a timer, because flattening an order
  that never filled opens the opposite position. Realized PnL stopped being described as an order-independent
  fold, because it depends on economic sequence even though average entry does not. Reversal fee treatment
  became a term of the provider contract rather than a universal claim that the processing fee is retained.
- Authority became a property of a quantity rather than of a process, with a compact `MIXED` form.
- References split from 42 files to 94 behind narrow triggers; reference-to-reference chains eliminated.
- `advanced/fin-matching-and-settlement` replaced by `fin-matching-engine` and
  `fin-market-data-publication`. Clearing, netting, DVP, settlement finality, liquidation waterfalls and
  venue-operated resolution were deleted rather than relocated.

## v0.3.0

- Six installed skills; the venue-side material moved to `advanced/` and out of the default install.
- Runtime context per activation cut from 479 lines to 194.
- The fixed seven-label output block was retired for per-finding output, emitted only where a finding exists.
- `T0`-`T3` replaced by two orthogonal fields: authority and exposure.

## v0.2.0

- Rules restated as economic propositions with the structural shape second and vendor literals last, so a
  rule survives renaming every identifier and changing language.
- The always-on guardrail block reduced from a second rule layer to a routing table.

## v0.1.0

- Initial suite.
