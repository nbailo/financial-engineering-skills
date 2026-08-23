# Mt. Gox — reconciling on an identifier the counterparty could change, and the famous explanation that the measurement refutes (2014-02)

**Domain:** Crypto exchange, withdrawal reconciliation | **Loss:** ~650,000–850,000 BTC claimed lost (750,000 customer-owned per the bankruptcy filing); the malleability mechanism blamed for it was measured at **1,811.58 BTC network-wide** across the whole preceding year | **Failure class:** Identity & idempotency | **Skill:** fin-onchain

## What happened

Mt. Gox halted bitcoin withdrawals on 7 February 2014, issued a press release on 10 February blaming
transaction malleability, suspended trading on 24 February and filed for bankruptcy on 28 February,
claiming approximately 850,000 BTC lost. 199,999.99 BTC were later found in an old wallet unused
since June 2011.

**This entry teaches a real and important rule, and explicitly refuses the causal story attached to
it.** The rule about mutable identifiers is correct and is established by the same paper that
refutes the Mt. Gox explanation. The claim that malleability caused Mt. Gox's loss is false. Both
things are in the primary source, and separating them is the point of the entry.

## Root cause, in code terms

**The mechanism, which is real.** Before SegWit, a bitcoin transaction's signature encoding was
malleable: a third party could rewrite it — without the private key and without invalidating the
transfer — producing a transaction that moved exactly the same value from the same inputs to the
same outputs, but with a **different transaction identity hash (txid)**. If the malleated version
confirmed first, the original txid never appeared on chain.

The exploitable defect is on the *victim's* side, and Decker and Wattenhofer state it precisely: to
be exploitable, "the victim also has to rely **solely on the transaction identity hash** to track and
verify its account balance."

The code shape is a withdrawal pipeline that:

1. constructs and broadcasts a withdrawal transaction,
2. records the txid it computed locally as the authoritative key for that withdrawal,
3. polls the chain for that txid,
4. and — this is the fatal step — treats **"txid not found"** as **"the withdrawal did not
   happen"**, then re-credits the user's balance or re-sends.

Every step but the last is reasonable. The last one conflates *absence of evidence for a key I chose*
with *absence of the event*, on a key the counterparty controls. A user who malleates their own
withdrawal receives the coins, sees the exchange conclude that the send failed, and is credited
again.

**Bitcoin Core was never vulnerable, and the reason is the rule.** Core "tracks the unspent
transaction output set by applying all confirmed transactions to it, **rather than inferring only
from transactions it issued**." It reconciles against authoritative external state, not against a
local log of instructions it believes it sent. That is the entire difference, and it is a design
choice available to any system.

**And the refutation.** Decker and Wattenhofer instrumented the whole network and measured
malleability directly. In the year up to the withdrawal freeze on 7 February 2014 they observed
**421 conflict sets totalling 1,811.58 BTC** across the entire network — three orders of magnitude
short of 850,000. The 286,076 BTC of malleability attacks they did observe occurred on 10–11
February, **after** Mt. Gox's press release: copycats reacting to the announcement, not the cause of
the loss it announced. WizSec's later analysis concluded the coins had been drained from the hot
wallet gradually starting in late 2011.

## The invariant that was violated

```
# the reconciliation key
reconciliation_key is immutable AND not counterparty-controllable
NOT: reconciliation_key := txid          # pre-SegWit, malleable by any third party

# the direction of truth
balance := apply(confirmed_transactions_from_authoritative_state)
NOT: balance := infer_from(local_log_of_instructions_I_believe_I_issued)

# and the one that turns a lookup miss into money
lookup(key) == NOT_FOUND  =>  state = INDETERMINATE
NOT:                          state = DID_NOT_HAPPEN
and NEVER: NOT_FOUND => automatically re-send or re-credit
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes, for the mechanism — and this is a pattern that recurs far outside crypto.**

The reviewable shape has three parts, and any one of them is a finding:

1. **A withdrawal or payment record whose primary key is an identifier derived from data the
   counterparty can alter.** In the bitcoin case that is a pre-SegWit txid. The general form is any
   key computed from a payload that a third party can re-encode — a signature-derived hash, a
   provider reference echoed back from a mutable field, a hash over a canonicalisation the other
   side controls. The correct key is one *you* generate before sending: a client-side idempotency
   key or client order ID, persisted before the network call.
2. **Status tracked by looking up your own recorded identifier and treating "not found" as "did not
   happen".** This is the direct finding. A polling loop of the form
   `if not chain.get_tx(stored_txid): mark_failed()` is wrong on its face, because the lookup can
   miss for at least three reasons — it has not propagated, it was replaced, or it genuinely never
   happened — and the code collapses all three into the most dangerous one.
3. **An automatic re-send or re-credit on a "not found" result.** Any value transfer that retries
   automatically on a negative lookup is a duplicate-payment generator. The correct behaviour is to
   resolve against authoritative state — the chain's UTXO or account state, the venue's position
   report, the processor's record queried by *your* idempotency key — and never to act on the
   absence of your own identifier.

The generalisation is worth stating because most readers will never touch a blockchain: a webhook
handler keyed on a provider's reference, a payment reconciler keyed on a bank statement narrative, a
settlement matcher keyed on `(account, amount, date)` — all have the same defect. If the key can
change, or can collide, or is produced by someone else, then "I don't see it" is not information
about whether the money moved.

## The rule

> **MUST — Never key financial reconciliation on an identifier that a counterparty can change.**
> Use an identifier you generate before sending, persist it, and send it.

> **MUST — Reconcile balances and positions against authoritative external state** — the chain's
> UTXO or account state, the venue's position report, the processor's record — **never against a
> local log of instructions you believe you issued.**

> **MUST — A "not found" lookup result is indeterminate, not negative.** Never automatically
> re-send or re-credit a value transfer because your own identifier did not appear.

> **MUST NOT — Teach or cite this incident as "malleability caused the collapse of Mt. Gox."** The
> only rigorous measurement of the period refutes it. Teach the rule; cite the refutation.

## Sources

- **Christian Decker and Roger Wattenhofer, *Bitcoin Transaction Malleability and MtGox*,
  arXiv:1403.6676v1, 26 Mar 2014** — <https://arxiv.org/pdf/1403.6676>. **Primary — a direct
  network-wide measurement study** (this URL is the preprint; the peer-reviewed version appeared at
  Financial Cryptography 2014), and it establishes both halves of this entry. The **mechanism**: that a
  victim which "relies solely on the transaction identity hash to track and verify its account
  balance" is exploitable, and that Bitcoin Core is not, because it "tracks the unspent transaction
  output set by applying all confirmed transactions to it, rather than inferring only from
  transactions it issued". The **refutation**: in the year before the withdrawal freeze, only
  **421 conflict sets totalling 1,811.58 BTC** were observed network-wide, and the 286,076 BTC of
  malleability attacks occurred on 10–11 February 2014, *after* Mt. Gox's press release.
- **WizSec analysis (April 2015)** — concluded that most or all of the missing coins were drained
  from the hot wallet **starting in late 2011**, not by malleability in 2014. **Secondary**,
  consistent with and independent of the measurement above.
- **Timeline facts** (withdrawal halt 7 Feb 2014, press release 10 Feb, trading suspended 24 Feb,
  bankruptcy filing 28 Feb, ~850,000 BTC claimed of which 750,000 customer-owned, 199,999.99 BTC
  later found in an old wallet) are from Mt. Gox's own filings and contemporaneous reporting.
