# Scope adjudication: four domains that asked for their own skill

Four domains were researched after the taxonomy was decided, each by an agent asked to argue decisively
whether it justified its own skill: **prediction markets**, **custody/wallets**, **clearing/settlement**, and
**accruals/corporate actions**.

Three of the four argued for promotion to a first-class skill. That is the expected outcome when you ask a
domain expert whether their domain is important, and it is not by itself evidence. This document records the
adjudication and the reasoning, because silently overruling well-argued research is worse than disagreeing
with it in the open.

## The binding constraint

Skill *descriptions* are pre-loaded into the agent's system prompt for every installed skill. That listing has
a budget (1% of the context window in Claude Code, `min(2%, 8000 chars)` in Codex) and **it is shared with
every other suite the user has installed**, not reserved for this one. When it overflows, descriptions are
dropped *starting with the least-invoked skills*, which is always a newly installed suite.

The derived safe range is 5–9 skills per suite at 250–450 characters of description each. This suite ships
**seven**. Promoting all three claimants would make ten, and would spend roughly 1,200 additional characters
of a shared budget to serve audiences that overlap heavily with skills already in the set.

So the bar for an eighth skill is not "this domain is real and has distinct failure modes". Every domain in
finance clears that. The bar is: **an agent holding the existing skills would reason confidently and wrongly,
and no dispatch pointer could fix it.**

## Prediction markets → reference in `fin-exchange-integration`

**The strongest argument made, and it is a good one:** on a binary venue the general CLOB rules are not merely
incomplete, they are *inverted*. There is no short: you buy the complementary outcome. Two *bids* can cross
each other, and the venue mints the instruments to settle them. The no-arbitrage invariant spans two books.
Fee models are not `bps × notional` and can be wrong by two orders of magnitude, asymmetrically under an
identity the venue itself guarantees. An agent applying general priors produces a **wrong collateral number on
a correct-looking code path**.

**Why it is still a reference.** The failure is real but it is *reachable by dispatch*. Every one of these
mistakes is triggered by a literal token that is already in the developer's code or task text: `polymarket`,
`kalshi`, `conditionId`, `complete set`, `negRisk`, `payoutNumerators`. That is precisely the routing pattern
with a demonstrated working implementation: a dispatch row keyed on an observable literal, with an
anti-paraphrase imperative. The material is deep, stable, and lookup-shaped, which is the shape references are for.

**Therefore:** `fin-exchange-integration/references/prediction-markets.md`, reached by a mandatory dispatch row
on those literals, and the venue names appear in the skill's frontmatter description so the skill loads at all.
Promote to its own skill in v2 if usage shows the dispatch row is being skipped.

## Custody / wallets → `fin-onchain` broadens to own the wallet's own state

**The strongest argument made, and it changed the design:** custody software maintains a **third state** that
is neither the chain nor the ledger: the wallet's own view of which outputs are mine, which are spendable,
which nonce is next, which address index is next, which signing sessions are live. Almost every custody bug is
a divergence between that third state and one of the other two. *A skill with no name for the wallet's own
state cannot express these rules.*

The original decomposition claimed custody splits cleanly: keys, nonces and confirmation policy to `fin-onchain`,
balances and solvency to `fin-ledger`. **That claim was wrong**, and the brief demonstrates it: UTXO change
outputs and coin selection, derivation paths and gap limits, sweep and gas-tank architecture, memo/tag deposit
routing, and withdrawal batching with partial failure belong to neither. `fin-onchain` as originally scoped was
implicitly EVM-flavoured (nonces, logs, decimals) and had no account of UTXO transaction construction, where
a change output sent to a fee is a total, silent loss.

The brief also produced the sharpest counter-example in the batch: the rule "spendable ≠ confirmed" is true on
EVM forwarder architectures and **false** on memo-ID chains, where deposited assets are immediately spendable.
A skill that flattens those into one abstraction ships a rule that is wrong half the time.

**Why not an eighth skill.** The audience genuinely overlaps: the person writing a deposit detector is already
loading `fin-onchain` for reorgs, finality and log identity. Splitting forces them to load two skills for one
job, and creates a description collision on exactly the tokens that route them (`deposit`, `withdrawal`,
`address`, `confirmation`).

**Therefore:** `fin-onchain`'s scope is amended to state explicitly that it owns *the wallet's own state as a
third state distinct from chain and ledger*, with `references/custody-and-wallets.md` for UTXO construction,
derivation and gap limits, sweeps, memo routing, and withdrawal orchestration. The chain-model-conditional
rules key on an observable predicate (account model vs UTXO vs memo-ID) rather than being flattened.

## Clearing / settlement → split, mostly to `fin-matching-and-settlement`

Netting arithmetic, DVP models, settlement finality and the CCP boundary are the back half of a venue
operator's own pipeline, and that skill already carries `and-settlement` in its name. ISO 20022 message-level
identity (`EndToEndId`, `TxId`, `UETR`) and the recall/return flow are payments-rail concerns and go to
`fin-payments`, which already owns rail irrevocability.

No promotion: the audience is small, and unlike prediction markets there is no inverted default: an agent
without this knowledge is *incomplete*, not confidently wrong.

## Accruals / corporate actions → split by where the failure happens

**The argument for its own skill was a dependency-inversion one:** the same day-count and compounding rules
govern a bank loan, an Aave reserve, a margin balance and a perpetual funding leg. Putting them in `fin-ledger`
makes `fin-exchange-integration` and `fin-onchain` depend on the downstream adjudicator, which is backwards.

**The argument is correct about code architecture and irrelevant to skill loading.** Skills are not libraries;
there is no import graph to invert. The question is only which skill the developer has open when the bug is
written. So the rules go where the failure happens, and the shared lookup material goes where it is deepest:

| Concept | Owner |
|---|---|
| Interest and fee accrual as a **posting**, never a balance mutation; per-period idempotence | `fin-ledger` |
| Day-count fractions, business-day conventions, holiday calendars, unit periods | `fin-ledger/references/accrual-and-time.md` |
| Index / scaled-balance pattern; rebasing tokens breaking `balanceOf` assumptions | `fin-onchain` |
| Perpetual funding as an accrual, deduped by settlement id like a fill | `fin-exchange-integration` |
| Corporate actions: splits adjusting quantity **and** average cost so PnL is preserved; ex/record/pay dates | `fin-exchange-integration` |
| Back-dated entries into a closed period; bitemporality | `fin-ledger` |

Note this was already partly right: `fin-exchange-integration` owned funding, and `fin-money-core` owned time
and business date. The gap was the accrual-as-posting rule and the day-count tables, both now placed.

## Result

Seven skills, unchanged in count. Two scopes amended (`fin-onchain` gains the wallet's own state;
`fin-ledger` gains accrual-as-posting), three new reference files, and one mandatory dispatch row.

The honest summary: **one of the three claimants (custody) found a real hole in the decomposition, and the fix
was to widen an existing skill rather than add one.** The other two were correctly served by references. If
adoption shows prediction-market developers are not reaching their reference file, that is the strongest
candidate for the eighth skill in v2.
