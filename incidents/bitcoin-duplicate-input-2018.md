# Bitcoin duplicate-input inflation (CVE-2018-17144) — a check removed as redundant, and then the assertion that would have caught its absence removed too (disclosed 2018-09-17)

**Domain:** Blockchain consensus, monetary policy | **Loss:** none known — "We are unaware of any attempts to exploit this vulnerability" on mainnet; exploited on testnet after disclosure. Had it been exploited, the supply cap would have been broken. | **Failure class:** Missing conservation (via check removal), with change discipline | **Skill:** fin-onchain

## What happened

Bitcoin Core versions 0.15.0 through 0.16.2 would accept a block containing a transaction that
spent the same input twice — provided the output being double-spent had been created in an earlier
block — crediting the value twice and inflating the money supply. The same
transaction on 0.14.x hit an `assert` and crashed the node — a denial of service, loud and
survivable. The path from "loud crash" to "silent inflation" ran through two separate changes, made
years apart, by different people, each individually defensible. The bug was reported by "awemany" on
17 September 2018, patched within hours, binaries within about 36 hours, and fully disclosed on
20 September after the patch had been reverse-engineered. Fixed in 0.16.3 and 0.17.0rc4.

## Root cause, in code terms

**Change 1 (Bitcoin Core 0.14, PR #9049): an optimisation removed a duplicate-input check
believed redundant.** The check that a transaction's inputs are pairwise distinct had been added in
2012 (PR #443) and is expensive. PR #9049 "avoided a costly check during initial pre-relay block
validation that multiple inputs within a single transaction did not spend the same input twice".
The redundancy argument rested on the UTXO-updating logic, which — in the disclosure's words —
"has sufficient knowledge to check that such a condition is not violated in 0.14 [but] it only did
so in a sanity check assertion and not with full error handling (it did, however, fully handle this
case twice in prior to 0.8)." So the check was not moved to an equivalent guard; it was moved to an
*assertion*, which is a strictly weaker place to put a consensus rule.

The consequence in 0.14.x was still contained: spending the same input twice produced an
inconsistent state that tripped that `assert`, and the node crashed. That crash was the safety net.

**Change 2 (0.15.0): the assertion was weakened until it no longer detected the condition.**
Separately, "as a part of a larger redesign to simplify unspent transaction output tracking and
correct a resource exhaustion attack the assertion was changed subtly. Instead of asserting that
the output being marked spent was previously unspent, it only asserts that it exists." The
assertion was not deleted — it was narrowed to a predicate that the bad state satisfies.

**The residual case matters and is usually dropped.** On 0.15.x–0.16.2 the assertion still fired,
and the node still crashed, when the double-spent output was *created in the same block*. Inflation
was possible only when the output being double-spent "was created in a previous block", in which
case "an entry will still remain in the CCoin map with the DIRTY flag set and having been marked as
spent, resulting in no such assertion. This could allow a miner to inflate the supply of Bitcoin."

Neither change is obviously wrong on its own. The first removes a duplicate computation; the second
tidies an assertion. Composed, they remove both the check and the detector of the check's absence.
The Bitcoin Optech record notes that the bug was disclosed on 17 September, patched within hours,
and that after full disclosure it **was** exploited on testnet — the window between "patch
published" and "patch understood" is short.

## The invariant that was violated

```
# the invariant the removed check enforced
forall transaction t:  all inputs of t are pairwise distinct
    checked on EVERY path that can accept a block, with full error handling
    NOT: enforced only by an assertion, and not on the pre-relay block-validation path

# the deeper rule
a check is redundant only with respect to a SPECIFIC path
"already validated upstream" is a claim about which upstreams exist, not about the value

# the assertion rule
if a check is removed, the assertion that detects the state the check prevented MUST remain
    (and must fail closed, not merely log)
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes — both diffs, and this is one of the clearest "refuse this change" findings available.**

**Diff 1 — the removal.** A change that deletes a validation check with a comment or commit message
asserting redundancy is exactly what a reviewer exists for. The correct review comment is not "are
you sure?" but a specific demand: *name every path by which this data can reach this point, and show
that each one performs the check.* In this case, enumerating those paths produces the answer
immediately — pre-relay block validation was one such path, and the only thing left standing on it
was a sanity-check assertion. The reviewing question is mechanical and the answer is in the code.

**Diff 2 — the assertion.** Weakening or removing an `assert` in a value-conservation path is a
direct finding, and one an agent should treat as more serious than most refactors precisely because
assertions look like noise. An `assert` on a money path is documentation of an invariant plus its
enforcement; deleting it deletes both.

**And the composition.** This is the part worth teaching. Neither change is catastrophic alone; the
catastrophe is that Change 2 removed the detector for Change 1's consequence, years later, with no
one connecting them. The defence is a rule about what a removal must leave behind: when a check is
removed as redundant, the invariant it enforced is written down and a test is added that fails if it
is violated. That test survives both changes and would have failed on the second.

## The rule

> **MUST — When removing a validation check as "redundant", record the invariant it enforced and add
> a test that fails if the invariant is violated.** Never remove both the check and the assertion
> that would detect its absence.

> **MUST — "Redundant" is a claim about a specific set of upstream paths.** Before removing a check,
> enumerate every path by which the data can reach that point, and show that each one performs the
> check. A path that originates outside your process — a peer's block, a partner's webhook, a replay
> job — is a path.

> **MUST — Never weaken or remove an assertion on a value-conservation path, and never let an
> assertion be the *only* enforcement of one.** Narrowing `assert(unspent)` to `assert(exists)` is
> a removal: the bad state satisfies the surviving predicate. A consensus or conservation rule
> belongs in error-handled validation; where a crash is all you have, a crash is still a better
> outcome than silent inflation.

> **MUST — A validation that protects a monetary invariant must run on every path that can accept
> the data**, not only on the path most transactions take.

## Sources

- **Bitcoin Core, "Disclosure of CVE-2018-17144", 20 Sept 2018** —
  <https://bitcoincore.org/en/2018/09/20/notice/>. **Primary.** Establishes verbatim: PR #9049 in
  0.14 "avoided a costly check during initial pre-relay block validation … which was added in 2012
  (PR #443)", while the UTXO-updating logic "only did so in a sanity check assertion and not with
  full error handling"; that in 0.14.X the condition produces "an assertion failure and a crash";
  that in 0.15 "the assertion was changed subtly. Instead of asserting that the output being marked
  spent was previously unspent, it only asserts that it exists"; that on 0.15.X–0.16.2 the assertion
  **still fires** when the double-spent output was created in the same block, and that inflation is
  possible only where "the output being double-spent was created in a previous block"; fixed in
  0.16.3 and 0.17.0rc4, released 18 September 2018; and "We are unaware of any attempts to exploit
  this vulnerability."
- **Bitcoin Optech topic page, CVE-2018-17144** —
  <https://bitcoinops.org/en/topics/cve-2018-17144/>. **Primary-adjacent.** Establishes the
  timeline: disclosed by "awemany" on 17 September 2018; patched within hours; binaries within about
  36 hours; full disclosure on 20 September after the patch was reverse-engineered; and that the bug
  **was** exploited on testnet after disclosure.
- **Related entry in this catalogue:** [Bitcoin value overflow
  (CVE-2010-5139)](bitcoin-value-overflow-2010.md) — the same supply invariant, broken eight years
  earlier by validating a total that had already wrapped.
