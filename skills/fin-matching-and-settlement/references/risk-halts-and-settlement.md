# Pre-trade risk, halts, clearing and settlement

The controls a venue or a broker-dealer gateway must apply before an order reaches the book, what happens to the obligations already outstanding
when something trips, and the back half of the pipeline — netting, delivery-versus-payment, finality, liquidation waterfalls and event
resolution. Every number in this file is one you produce and nobody else can check for you.

## Contents

1. **SEC Rule 15c3-5, clause by clause** — the mandate per paragraph, *orders entered rather than executions obtained*, reject-don't-warn.
2. **Band derivation** — per-instrument reference price, one derivation per session state, Goldman's cross-universe bound, the sentinel price.
3. **Self-match prevention at the gateway** — account-family scope, four outcome semantics, counterfactual reporting.
4. **Fan-out bounds** — counters keyed to the inbound unit, the check on the emit path, the trip flag's owner.
5. **Kill switches exercisable faster than the loss accrues** — Compound's seven-day window, Goldman's lift-while-investigating, the FCA
   override-all button.
6. **The six meanings of halt** — the obligation state each level leaves behind.
7. **LULD and market-wide breakers** — band tiers, doubling window, reference cadence, Limit vs Straddle State.
8. **Auction, cross, and what a halt does to resting orders** — the Facebook IPO cross as the worked failure.
9. **Resumption, trade breaks and error positions** — the pre-resume checklist and bust semantics.
10. **Trade capture and netting arithmetic** — T+1 cutoffs, nine invariants as assertions, a worked cycle.
11. **Novation and the CCP boundary** — discharge rather than guarantee, and the bilateral window.
12. **DVP models 1/2/3** — the BIS taxonomy and what each guarantees.
13. **Settlement finality and the irrevocability point** — a legal predicate with a technical shadow.
14. **Liquidation, marks and the waterfall** — mark construction, maintenance margin, partial liquidation, insurance fund, socialised loss, ADL.
15. **Event resolution** — payout vectors, the dispute window, complete-set conservation, reversal after payout.

---

## 1. SEC Rule 15c3-5, clause by clause

17 C.F.R. § 240.15c3-5, adopting release Rel. 34-63241 (3 Nov 2010; 75 Fed. Reg. 69792). A specification, not a compliance narrative.

| Clause | Mandate (rule text) | In code |
|---|---|---|
| `(b)` | maintain a documented system of controls; **preserve a copy** of the procedures and a written description of the controls as records under 17a-4(e)(7) | Limits config and its change history are records, not deployment artefacts. Field-level audit log on every threshold. |
| `(c)(1)(i)` | "Prevent the entry of orders that exceed appropriate pre-set credit or capital thresholds **in the aggregate** for each customer and the broker or dealer … **by rejecting orders**" | Synchronous rejection in the submit path, keyed per customer **and** firm-aggregate. Not an alert. |
| `(c)(1)(ii)` | "Prevent the entry of erroneous orders, **by rejecting orders that exceed appropriate price or size parameters, on an order-by-order basis or over a short period of time, or that indicate duplicative orders**" | Three controls: per-order price/size band; a rate/aggregate window; **duplicate detection**, an explicitly enumerated regulatory requirement. |
| `(c)(2)(i)–(iii)` | no entry unless pre-order-entry regulatory requirements are met; block restricted securities; restrict system access to pre-approved persons and accounts | Regulatory gates run before the order is formed, not on the execution report. |
| `(c)(2)(iv)` | "Assure that appropriate surveillance personnel receive **immediate post-trade execution reports**" | Post-trade is an *additional* obligation. It does not discharge `(c)(1)`. |
| `(d)` | controls under the **direct and exclusive control** of the broker-dealer, narrow written-contract carve-out for `(c)(2)` items only, which "shall not relieve" the provider | A limit enforced only inside a vendor's or a client's process does not count. |
| `(e)(1)`, `(e)(2)` | at least annual documented review; **CEO annual certification** of compliance with `(b)` and `(c)` | The review is an artefact with a date and an author. |

**The measurement basis is the sentence that condemns every post-execution risk screen.** Adopting release: controls "must be applied on an
**automated, pre-trade basis, before orders are routed**", and the firm "**must assess compliance with the applicable threshold on the basis of
exposure from orders entered** … **rather than relying on a post-execution, after-the-fact determination** … **on the basis of orders entered
rather than executions obtained**." So the exposure counter increments at `submit`, not at `fill`, and decrements on ack'd cancel, reject or
expiry — never on a fill alone. An engine that sums executions measures the wrong quantity by construction, and the gap is the size of your open
working order book. Goldman ¶12 is the counter-example: capital utilisation "**only calculated … every 30 minutes**", alerting at 75%, with
"**no automated process to prevent the entry of additional orders**" on breach. **The price collar is the release's own example**: "a
**systematic, pre-trade control reasonably designed to reject orders that are not reasonably related to the quoted price of the security**" (fn
89 cites NYSE Arca Rule 7.31(a) and Nasdaq Rule 4751 market-order collars). Note *the security*, singular — see §2. Duplicate controls are
per-counterparty (fn 87: one for an HFT "may very well be different – in particular, **more tolerant**" than one for retail). The rule is **not
a halt mandate** — it mandates per-order synchronous rejection — and it is a floor: the `(c)` controls "**should not be viewed as a
comprehensive list**".

```python
def submit(order, acct):                                  # route() unreachable except through here
    ref = reference_price(order.instrument)               # §2 — per instrument, never cross-universe
    if ref is None: return Reject("NO_REFERENCE_PRICE")   # missing input rejects; never defaults
    if not within_band(order.price, ref, band_for(order.instrument, session_state)):
        return Reject("PRICE_NOT_REASONABLY_RELATED")     # (c)(1)(ii)
    if dup_window.seen(order.fingerprint, acct.dup_tolerance):
        return Reject("DUPLICATIVE_ORDER")                # (c)(1)(ii), calibrated per account
    proposed = exposure[acct] + notional(order)           # ORDERS ENTERED, not executions obtained
    if proposed > acct.credit_limit or firm_exposure + notional(order) > firm_capital_limit:
        return Reject("CREDIT_OR_CAPITAL_THRESHOLD")      # (c)(1)(i)
    exposure[acct] = proposed                             # commit the increment with the send decision
    return route(order)
```

## 2. Band derivation

The band is a function of **that instrument's own reference price** and the session state. One derivation, called from every session-state code
path.

| Session state | Reference price source | What a special case costs you |
|---|---|---|
| Continuous | NBBO / venue best, or the LULD reference price | — |
| Pre-market / post-market | prior session close **for that instrument**; if unavailable, reject | Goldman: a "default" branch replaced the per-series band entirely |
| Auction / cross | indicative price or the auction collar reference | orders priced off a stale continuous reference enter the cross |
| Halted | last valid reference before the halt, frozen | a reference updating from a paused feed drifts |

Goldman Sachs, 20 Aug 2013 (SEC order). In-hours Sigma Options used ±100% of NBBO below $1 and ±50% at or above $1 — per-series. Pre-market it
took another branch: any price "greater than $0.01 and less than 1.5 times the highest closing price from the prior day **for any listed
option**" (¶25), so the $1 orders passed because they "**fell between $0.01 and $3,090**" (¶30). A bound aggregated over the entire universe of
listed options is vacuous for every instrument except the most expensive. ~1.5m contracts executed, potential loss up to $500M, actual ≈$38M
after clearly-erroneous busts; ¶27 records three prior erroneous pre-market orders (Nov 2011 – Aug 2013) that prompted no re-evaluation. The
sibling failure is a **sentinel price**. FCA Final Notice, Citigroup Global Markets, 17 May 2024, ¶4.27: the benchmark index price came from an
unavailable external feed, so it "**defaulted to -1**", the screen rendered `quantity × -1 = -58,000,000`, and the trader saw the number they
expected; with the feed up the same field would have read ≈US$444bn. ¶4.30: the same missing data blanked the one basket-level soft block — "Due
to lack of market data, Wave notional cannot be found" — and it proceeded anyway. A missing price rejects; it never substitutes a sentinel, and
a sentinel is never multiplied.

## 3. Self-match prevention at the gateway

Scope is the **account family**, not the strategy. CFTC v. Coinbase, March 2021, $6.5M: two internally operated programs "matched orders with
one another … resulting in trades between accounts owned by Coinbase", and that volume propagated into CME's Bitcoin Real Time Index,
CoinMarketCap and the NYSE Bitcoin Index. Four incompatible outcome semantics ship today — decrement-both (Nasdaq AIQ "Decrement both", Coinbase
`dc`, the default, which cancels both on equal sizes), cancel-oldest (AIQ "Cancel oldest", Coinbase `co`), cancel-newest (`cn`), cancel-both
(`cb`) — and Coinbase documents that **the taker's STP instruction takes precedence** where the two sides differ. No neutral default exists:
pick one, publish it, make it a per-account attribute. **A prevented match is not a trade** — emit it as a counterfactual (Nasdaq's field is
`Quantity prevented from trading`), never as a fill to either side, and exclude it from published volume.

## 4. Fan-out bounds

A bounded transformation carries a counter keyed to its **inbound unit** and a hard bound, checked on the emit path before the send — not by a
monitor reading a metric.

| Bound | Keyed to | Trips when |
|---|---|---|
| `max_children_per_parent` | parent order id | a router loops |
| `max_notional_per_parent` | parent order id | a sizing bug scales |
| `max_shares_vs_ADV` | instrument + parent | the order is large relative to the market, not to itself |
| `max_messages_out_per_message_in` | inbound message id | any fan-out amplifier |
| firm-aggregate notional | firm | the per-item limits are individually satisfied |

The last row is the FCA/CGML finding (¶4.18(a), ¶4.33): hard blocks were **per item** — US$2bn order notional, 200m shares — and US$196bn passed
through 349 orders; "had a **basket level** wave notional hard block limit been in place … the trading incident would not have occurred."
Per-item limits are satisfiable by an unbounded number of items. On breach: set a flag the emit path reads before every subsequent send; cancel
resting orders; disconnect the order-entry session; keep risk, position and drop-copy alive. **The flag is not resettable by the component that
tripped it.** Knight ¶21 — no "control to compare orders leaving SMARS with those that entered it", and "no procedures in place to halt SMARS's
operations in response to its own aberrant activity"; ¶27, "continued to send millions of child orders while its personnel attempted to identify
the source", with the remediation deploying the defective code to seven more servers: "This action worsened the problem." ¶23/¶24: the "33
Account" holding the accumulating position had a $2m limit "linked to no automated controls" — so the error account is a real account with an
owner, an aging policy and a hard escalation threshold, wired to a firm-aggregate limit that **rejects new orders** on breach.

## 5. Kill switches exercisable faster than the loss accrues

A kill switch whose exercise latency exceeds the loss rate is documentation. Measure the switch in the same units as the loss. **Compound,
Proposal 62, 29–30 September 2021.** The upgraded Comptroller (introducing `compSupplySpeeds` / `compBorrowSpeeds`) let users claim far more
COMP than they had accrued. Per Compound's founder, "there are no admin controls or community tools to disable the COMP distribution", and any
change required the **7-day governance process**. ~168,000 COMP (≈$50M) was actually claimed; bounded worst case ~280,000 COMP (≈$80–83M),
bounded only because the Comptroller held a limited balance with the rest in the Reservoir. Everyone watched the drain for a week. *(The `>` vs
`>=` root cause is SECONDARY — CryptoSlate / The Block, credited to auditor Kurt Barry; the seven-day window and the absent admin controls are
from the founder's own statement.)* Generalised: **every path that distributes, mints or transfers value has a pause exercisable faster than the
loss accrues.**

**Automatic to trip, manual to clear.** Goldman ¶8: rate-based circuit breakers halted all message traffic above a rate — they worked. Then "the
firm's control personnel **repeatedly lifted the circuit breakers blocks between 8:44 a.m. and 9:32 a.m.** … **did not obtain authorization**
from the responsible technology employees, as required under written firm policies." ¶31: the employee who lifted the block at 09:01 **had
authored the circuit breaker policy** he was violating; ¶9 records that lifting blocks "while still investigating the cause" was pre-existing
practice. A reset therefore demands a named authorising role **plus a recorded root-cause determination** — require `cleared_by` and
`root_cause_id`, refuse a bare `resume`. **A warning that can be bulk-overridden is not a control.** FCA/CGML ¶4.28–¶4.31: one pop-up presented
**711 warning messages**, only the **first 18 lines** visible without scrolling, and two buttons — "Override soft warnings" and "Cancel all".
¶4.15: "The system did not require a trader to scroll down through the list of warning messages." One binary clearing N warnings has an
effective threshold of infinity.

## 6. The six meanings of halt

`halt ⇒ engine quiesced ∧ everything already produced delivered or explicitly voided`, at the smallest scope that contains the breach. Name the
level in the code and in the incident channel.

| # | Level | Existing obligations | Reset authority | Use for |
|---|---|---|---|---|
| 1 | Reject this operation, typed | untouched | n/a — per-call | a bad inbound order |
| 2 | Freeze one aggregate (symbol, account) | untouched | operator, scoped | one book's invariant |
| 3 | Fail-closed asymmetric: no new or increasing exposure; cancel / close / flatten / settle / reconcile stay hot | actively managed | successful reconciliation, never a timer | open exposure at detection |
| 4 | Cancel-all + disconnect order entry; risk, position, drop-copy stay up | actively managed | named role + root cause | a fan-out bound breached |
| 5 | Quiesce: stop accepting **and** producing, drain in-flight, deliver or explicitly void everything produced | drained, then frozen | named role + root cause | venue-level halt |
| 6 | Process abort | **abandoned** | n/a | only where nothing is in flight |

**Severing the transport is not a halt.** TSE, 1 Oct 2020: the participant network was cut at 08:54 while arrowhead kept matching, accumulating
executions nobody could see; the report records "we had not prepared a contingency plan to halt trading in the case of…". Cutting the network
does not stop the matching engine; it only stops you finding out. **Risk-reducing paths are gated by a different flag than the risk-increasing
path**, with a test that exercises them while halted — LULD is the reference design, since even in a Trading Pause the **closing transaction
still executes** and an unexecutable quote is disseminated "with an indicator identifying it as unexecutable" rather than withheld. Give an
invariant that can be **momentarily** false during a named intermediate state a bounded self-heal window before escalating (LULD waits 15
seconds in Limit State).

## 7. LULD and market-wide breakers

LULD is derived state, not delivered state. You compute it; a wrong `>=` changes when a market stops.

| Element | Value (LULD NMS Plan) |
|---|---|
| Tier 1 band, price > $3.00 | ±5% |
| Tier 1 band, $0.75–$3.00 | ±20% |
| Tier 1 band, < $0.75 | lesser of $0.15 or 75% |
| Tier 2 band, price > $3.00 | ±10% |
| Doubling window | bands doubled in the **last 25 minutes** (Tier 1 and sub-$3 Tier 2) |
| Reference price | arithmetic mean of eligible reported transactions over the **prior five minutes** |
| Reference update rule | updated only on a **≥1% move**, recomputed every **30 seconds** |
| Limit State | NBO (NBB) **equals but does not cross** the Lower (Upper) band |
| Limit State exit | within **15 seconds**, all Limit State Quotations executed or cancelled **in their entirety** |
| Pause | 15 seconds in Limit State → **five-minute** pause by the primary listing exchange |
| Straddle State | NBB or NBO outside the bands **without** a Limit State |

Every row is an off-by-one opportunity: *equals* versus *crosses*; 15 seconds of wall clock versus 15 seconds of market-data time; "recomputed
every 30 s" and "updated on ≥1% move" as two predicates that must both hold. Read the clock from the market-data timestamp inside the
deterministic core, not from `now()`, and publish the state transitions rather than making consumers infer them — Nasdaq's ITCH carries LULD
Auction Collar and MWCB Decline Level / Status as explicit messages for exactly this reason. *(Tier 2 sub-$3.00 bands and the MWCB percentages
are not established by the sources read here; take them from the plan text.)*

## 8. Auction, cross, and what a halt does to resting orders

A halt or a cross changes what the order-entry protocol will accept, and that change is part of the contract. Nasdaq's OUCH exposes `Cancel
Pending` and `Cancel Reject` **only for the cross late period** — a defined window where a cancel is neither accepted nor rejected
synchronously. Kalshi documents the opposite convention on reactivation: on `inactive → active`, "**All resting orders are cancelled on this
reactivation**", and past `close_time`, "**all order operations, including cancellations, are rejected with `MARKET_INACTIVE`**". Three answers
are defensible — resting orders persist into the reopening auction; they are cancelled at the halt with an explicit per-order cancel; or they
persist while cancels are queued and acknowledged only after the cross prints. Acknowledging a cancel and then filling the order is not one of
them.

**The Facebook IPO cross, SEC Rel. 34-69655 (29 May 2013), is the worked failure.** Pipeline: the IPO Cross Application computes (price, volume)
over the order set; the matching engine runs a validation check that no order used in the computation was cancelled during it; on failure,
recompute.

> "This second calculation … **incorporated only the first cancellation received during the first calculation** … Thus, **if there were
> multiple orders cancelled during the first IPO Cross Application's calculation, the validation check performed after the second
> calculation would fail again**." (¶9)

> "because the system was designed to perform a separate recalculation for each of those cancellations, the validation check failed
> each time. As it did so, even more cancellations came in … **A loop resulted.**" (¶20)

The retry advanced the input cursor by **one cancellation**, not to the tail of the queue, so convergence required the cancellation arrival rate
to fall below one per recomputation. Load-tested to 40,000 orders; members entered **over 496,000** (¶12); one calculate-plus-validate pass took
**20 ms** against a usual **1 to 2 ms** (¶17). Everything else follows from that one property:

- **The mitigation was deleting the check, live** — a failover to a duplicate matching engine with "**several lines of code that configured the
  validation check function**" removed, used "as the vehicle for launching a new, modified version" rather than as a duplicate, untested for
  this situation (¶23).
- **The livelock built a 19-minute input backlog inside the primary.** The failover crossed at 11:30:09 over the book "**up until 11:11 a.m.**",
  excluding **more than 38,000** marketable orders; ~8,000 hit the market at 11:30, **more than 30,000** became "stuck" (¶26).
- **The error position came from the cancel imbalance in that window, not from the code removal**: "more sell shares than buy shares were
  cancelled during this period" → a **>3 million share short**, ≈$129M (¶28), ≈$10.8M profit through the error account (¶40), NES net capital
  deficiency ≈$26.5M (¶42).
- **Acknowledged cancels were knowingly dishonoured.** ¶24 fn 4: telling members their orders "had not been successfully cancelled, **even
  though NASDAQ's system had, immediately upon submission, acknowledged these members' cancellations**" — "**was not discussed by any of the
  participants**".
- **The downstream reconciliation worked and was ignored.** The Execution App could not reconcile its share count and "marked the cross as being
  in error and **did not disseminate confirmations**" (¶30); a 6.3m share gap between indicative volume (82m) and the print (75.7m) went
  unaddressed "during the minutes and hours following the cross" (¶27).

**The remediation NASDAQ agreed to (¶65) is the rule:** "For IPO and Halt Crosses, NASDAQ will **close its order ports to new Cross orders and
cancels** … **after the calculation of the Cross is triggered.** For Opening and Closing Crosses, NASDAQ will … **take into account bursts of
changes … in one recalculation of the Cross rather than in multiple recalculations.**" Freeze the input set, or drain the entire pending queue
into one recomputation; a retry ceiling would have aborted the cross, not completed it. Assert input-set freshness at commit: carry the sequence
number of the last consumed event into the cross record and compare it to the queue tail before printing.

## 9. Resumption, trade breaks and error positions

Decide the fate of in-flight and undelivered executions **before** resuming, not during:

1. Every execution produced before the halt is either delivered to both counterparties or explicitly voided with a Trade Cancel referencing its
   original match number. No third state.
2. The published sequence has no gap and no un-emitted committed event; replay of the persisted command stream reproduces the emitted sequence
   byte for byte.
3. Book state is derived from the journal, not from memory that survived the incident.
4. The trip flag is cleared by a named role with a recorded root-cause determination.
5. Any check bypassed during the incident is back on, and every output produced while it was off is quarantined and reconciled before being
   treated as authoritative.

TSE/JPX escalated to a **whole-day** halt on 1 Oct 2020 precisely because participants held undelivered fills and no rule existed for post-halt
resumption. Escalation to venue scope is correct only when you cannot establish what counterparties hold — the failure mode of not having
written step 1 down in advance.

**A bust is out-of-band from the book and final.** ITCH: "A trade break is final; once a trade is broken, it cannot be reinstated"; it
references a Match Number from a previously transmitted execution, and a firm using ITCH only to build a book "may ignore these messages as they
have no impact on the current book". OUCH: "You will always get an Executed Order Message prior to getting a Broken Trade Message for a given
execution", reasons `E` erroneous, `C` consent, `S` supervisory, `X` external; FIX's equivalent is `ExecType` Trade Cancel / Trade Correct with
`ExecRefID` pointing at the corrected `ExecID`. **Position and P&L are revisable after the fact; the book and the journal are not** — anything
computing P&L, margin or tax from fills must accept a retroactive bust, as >20,000 trades were on 6 May 2010.

## 10. Trade capture and netting arithmetic

Trade capture is the hand-off from the match to the obligation. Rule 15c6-1(a): US securities settle no later than "**the first business day
after the date of the contract**". Rule 15c6-2(a): allocation, confirmation and affirmation complete "**as soon as technologically practicable
and no later than the end of the day on trade date**" — operationally (adopting release fnn. 267, 370) allocations by **7:00 p.m. ET on trade
date** and the ITP affirmation cutoff at **9:00 p.m. ET on trade date**, moved from 11:30 a.m. ET on T+1. An unaffirmed trade at 21:00:01 does
not merely become late: fn 268, it "will likely require a participant to **submit the transaction manually to DTC**", and a failure to settle
"**may be subject to buy-in obligations**". Model the cutoff as a step function selecting a different downstream path. For a netting cycle `C`,
currency `X`, and the set `G` of gross obligations admitted to `C`:

| Id | Invariant | Assertion |
|---|---|---|
| I-1 | pairing | `Σ debit(g) == Σ credit(g)` over `g ∈ G, ccy == X` |
| I-2 | zero-sum net — **this is the one** | `Σ_p net(p, C, X) == 0` |
| I-3 | per-participant definition | `net(p) == Σ credits to p − Σ debits from p`, over exactly `G` |
| I-4 | exhaustive disjoint partition | `∀g: |{C : g ∈ C}| == 1` |
| I-5 | exposure reduction | `Σ_p |net(p)| ≤ Σ_g amount(g)` |
| I-6 | no cross-currency netting | per currency; EUR vs USD needs an explicit FX obligation with a recorded rate |
| I-7 | determinism | recomputing from the same `G` yields identical nets — no iteration order, no float, no clock |
| I-8 | settlement equality | after settlement `posted(p, C, X) == net(p, C, X)` and `Σ_p posted == 0` |
| I-9 | offset accounting | `Σ_g amount(g) == Σ_p max(net(p), 0) + offsets`, with `offsets` **materialised** |

Worked cycle, USD: `A→B 100`, `B→C 60`, `C→A 40`, `A→C 25`. Then `net(A) = 40 − 125 = −85`, `net(B) = 100 − 60 = +40`, `net(C) = 85 − 40 = +45`.
I-2: `−85 + 40 + 45 == 0` ✓. I-5: `Σ|net| = 170 ≤ Σ gross = 225` ✓. I-9: the one-sided settlement amount is `Σ max(net,0) = 85`, so `offsets =
225 − 85 = 140` — the value extinguished by netting, and materialising it is what lets you answer "why did we move less than we owed". `Σ_p
net(p, C, X) == 0` is one line to assert and must be asserted **before any settlement instruction is emitted**; nonzero means the cycle created
or destroyed money in `X`.

The legal consequence is a code constraint. SFD (Directive 98/26/EC) Art. 2(k) defines netting as conversion "into **one net claim or one net
obligation** … **with the result that only a net claim can be demanded or a net obligation be owed**" — the gross obligations are no longer
separately enforceable, so a collections, dunning or liquidity-forecast job still reading the gross ledger double-counts the participant. Art.
3(2): "**No law, regulation, rule or practice … shall lead to the unwinding of a netting**" for a designated system, so "unwind and recompute
without the failing member" is not an available error path. CSDR writes the same zero-sum shape into law: RTS Art. 17 requires CSDs to collect
and distribute "**the net amount**" of cash penalties, and Art. 7(2) — "**The cash penalties shall not be configured as a revenue source for the
CSD.**" So `Σ collected == Σ distributed`, with the mechanism's costs as a separate fee stream (Art. 18).

## 11. Novation and the CCP boundary

PFMI glossary: **novation** is "A process through which the original obligation between a buyer and a seller is **discharged** through the
substitution of the CCP as seller to the buyer and buyer to the seller, **creating two new contracts**"; **open offer** interposes the CCP "**at
the time a trade is executed**". Discharge, not guarantee: before novation A owes B; after, A owes the CCP and the CCP owes B, and the A↔B
obligation **no longer exists** — exposure, margin, netting sets and default management key on the CCP, and the original counterparty identity
becomes reference data, useful for reporting and useless for credit. The *moment* differs by model and the gap is a real bilateral window: open
offer novates at execution, classic novation at clearing acceptance, so a system that assumes cleared trades carry no counterparty risk from the
execution timestamp is wrong for classic-novation venues. Carry `novated_at` and `novation_model` on the trade; do not infer either.

## 12. DVP models 1/2/3

BIS CPSS *Delivery Versus Payment in Securities Settlement Systems* (1992), §3.2:

| Model | Securities | Funds | Guarantees | Hazard |
|---|---|---|---|---|
| 1 | gross, trade-by-trade | gross, trade-by-trade | delivery final "**at the same time as**" payment; §3.3 "All transfers are final (irrevocable and unconditional) … at the instant the debits and credits are posted"; "Overdrafts (negative balances) on securities accounts are **prohibited**" | §3.4 "high rates of failed transactions"; "In an extreme case, a high fail rate could escalate to a **gridlock** situation" |
| 2 | **gross**, final "throughout the processing cycle" | **net**, final "at the end of the processing cycle" | delivery certainty for the buyer | §1.11 "**final securities transfers precede final funds transfers** … clearly has the potential to expose sellers of securities to **substantial principal risk**" — the seller has irrevocably delivered and holds a claim |
| 3 | **net**, end of cycle | **net**, end of cycle | §1.12 "can eliminate principal risk by ensuring that final transfers of securities occur **if and only if** final transfers of funds are made" | §1.15 "**Some model 3 systems, by contrast, do not guarantee settlement**" — the unwind, where one member's failure retroactively changes everyone's obligation |

BIS's headline conclusion, §1.10: protection against principal, replacement-cost and liquidity risk "**depends more on the specific risk
management safeguards a system utilises than on which model is employed**". The label is not the safety property. PFMI Principle 12, KC1 states
it as an iff over **final** settlement: "the final settlement of one obligation occurs **if and only if** the final settlement of the linked
obligation also occurs, **regardless of whether the FMI settles on a gross or net basis and when finality occurs**." DVP is not "both legs at
the same instant"; it is "neither leg is final unless both are". A two-phase commit over *provisional* postings satisfies the simultaneity
intuition and not the iff, and the iff is what eliminates principal risk . The shape to name in review is **accidental Model 2** — delivering
securities gross for latency and batching the cash for efficiency, with no guarantee. That is what Herstatt was: two legs of one trade, two
systems, two business days, two finality instants, no conditionality between them.

## 13. Settlement finality and the irrevocability point

PFMI §3.8.1: "**Final settlement** is defined as the **irrevocable and unconditional** transfer of an asset or financial instrument, or the
discharge of an obligation …", fn 86: "**Final settlement (or settlement finality) is a legally defined moment.**" §3.8.4: "An FMI's **legal
framework and rules** generally determine finality … including the insolvency law … **a well-reasoned legal opinion is generally necessary to
establish the point at which finality takes place**." SFD Art. 3(3): "**The moment of entry** of a transfer order into a system **shall be
defined by the rules of that system.**" Art. 5: "A transfer order **may not be revoked** … **from the moment defined by the rules of that
system.**"

1. **Entry and irrevocability are two distinct rule-defined instants.** Carry both: `entered_at_seq` (your own total order) and
   `irrevocable_at`. Do not collapse them into `created_at`.
2. **Your code records which side of the instant each instruction is on; it does not invent one.** Whether an order made it in before a cutoff
   or an insolvency instant is a fact about *your own ordering*, which must therefore be durable, total and auditable.
3. **Back-dating fails the principle explicitly.** PFMI §3.8.5: a system not designed to settle on the value date "would not satisfy this
   principle, **even if the transaction's settlement date is adjusted back to the value date after settlement**".

Concrete instances: T2 settles payment orders "one by one on a continuous basis in central bank money, with **immediate finality**"; FedNow
"settles with finality when the FedNow Service records the debit and credit", and has **no reversal primitive** — a return is a new payment.

## 14. Liquidation, marks and the waterfall

**Three prices, three jobs.** A thin book or a wick on one venue moves *last price*; it must not move the number the liquidation engine reads,
and the code should say which price each calculation uses. **A hybrid mark is a feedback loop you must bound**: Hyperliquid's mark price
"combines external CEX prices with Hyperliquid's own book state", so the venue's own liquidations move the price that triggers liquidations — a
purely internal price is worse, a purely external one is manipulable from outside, so bound the internal component's influence and write the
loop down in the risk engine.

**The mark is an input an adversary can pay to move.** Size risk limits so the cost of moving the mark exceeds the payoff of the position it
liquidates, and cap the notional a market's own oracle can collateralise (open-interest caps, per-asset borrow caps, liquidity-adjusted
haircuts). Mango Markets: the oracle was inflated >13× in 30 minutes by self-trading. Hyperliquid JELLY, 26 March 2025: self-trading against a
thin oracle forced the HLP backstop vault into a large short, and the market was then delisted and settled at a chosen price. **A backstop that
can be forced to inherit a position is the attack target, not a safety net.**

| Step | Trigger | Mechanism (Hyperliquid's documented parameters as the concrete instance) |
|---|---|---|
| Maintenance margin | equity below MM | MM = **half the initial margin at max leverage**, i.e. 1.25%–16.7% |
| Market liquidation | MM breach | first attempt is a **market order into the book** |
| Partial liquidation | position > **100k USDC** | **20% at a time**, **30-second cooldown** |
| Backstop liquidation | equity at **2/3 of maintenance margin** | position transfers to the liquidator vault (HLP); **cross** positions transfer entirely, **isolated** transfer position + margin |
| Insurance fund draw | backstop position closed at a loss | fund absorbs the deficit |
| Socialised loss / ADL | fund exhausted | loss allocated to solvent counterparties, or profitable opposing positions closed at a published price |

Liquidate in bounded tranches with a cooldown rather than as one market order — a single sweep prints through the book and re-triggers the mark
that triggered it. **ADL ranking is [UNVERIFIED] here**: the commonly repeated formula (profit × effective leverage) could not be confirmed
against a primary operator document in this corpus, and Hyperliquid's liquidation page does not document ADL at all. Do not hard-code a ranking
you have not read in the venue's own specification; if you are the venue, publish yours. Whatever the step, **publish it with the inputs that
selected it** — the deleveraged counterparty cannot see the fund balance, the ranking or the mark you used — and give venue-initiated fills an
explicit marker (Binance uses client-ID prefixes `autoclose-`, `adl_autoclose`, `settlement_autoclose-`; Bybit sends them with an **empty
`orderLinkId`**). **A liquidation is an execution**: same journal, same sequence numbers, same checked aggregates, same publish check as any
other match.

## 15. Event resolution

**Model resolution as a payout vector with a denominator** — persist `(numerators[], denominator)` — never as a winning-outcome enum or a `bool
won`. Gnosis CTF `reportPayouts` requires only `den > 0`, so `[1,1]` (a 50/50) and `[3,1]` (75/25) are valid; Polymarket narrows this, with
`UmaCtfAdapter._constructPayouts` accepting exactly `{0, 0.5e18, 1e18}`. Two initialisation sentinels are in play and they differ:
`payoutNumerators[id].length == 0` means *not prepared*; `payoutDenominator[id] == 0` means *not resolved*. Credit only on the explicit finality
signal — `payoutDenominator[conditionId] > 0` on the CTF, `status == finalized` on Kalshi — and keep trading-halted and payout-known as separate
fields, since Kalshi's `status=closed` matches everything past `close_time` that is not `finalized`.

**The dispute window is not a timer you can hard-code.** Kalshi models it as first-class state — `initialized / active / inactive / closed /
determined / disputed / amended / finalized`, with "`amended`: Re-determined after a dispute. **Settlement timer restarts.**", `finalized` the
only terminal state, and `settlement_timer_seconds` exposed in the REST response. On the UMA side, Polymarket's user docs say a "2-hour
challenge period" while UMA's integration page describes `ManagedOptimisticOracleV2` with a proposer whitelist and operator-ended extensions
"not a fixed length", minimums as low as ~15 minutes for unflagged sports. The two primary sources disagree: read liveness from the request, and
treat the window as open-ended for risk. A dispute also **discards the first proposal outright** — "**The settlement of the prediction market
then ignores the 1st request and only resolves as per the 2nd request.**" The adapter implements this in `priceDisputed` → `_reset`, overwriting
`questionData.requestTimestamp` with `block.timestamp`, so a cached "proposed outcome" is not merely stale — it is the wrong answer, and its
timestamp addresses a dead request. Key every price lookup by the **current** `requestTimestamp`. **Reversal after payout depends on the venue,
and the two are opposites.** Kalshi has a re-determination path (`determined → disputed → amended`). The CTF has none: `reportPayouts` guards
are `require(payoutNumerators[conditionId].length == outcomeSlotCount)`, `require(payoutDenominator[conditionId] == 0, "payout denominator
already set")` and per-slot `require(payoutNumerators[conditionId][i] == 0)`. **No re-report, no correction, no admin override.** Build the
reversal path *above* the CTF — a new condition, or an off-ledger make-whole against a named account. The March 2025 Ukraine-minerals market is
the worked case: the resolution path executed exactly as specified, `payoutDenominator` was written to 1, and the operator declined refunds on
the grounds that it was not a "market failure" while conceding the market "was resolved too soon". The question is not "how do I stop a whale";
it is "what does my ledger do with a payout the venue agrees is wrong and will not reverse".

**Complete-set conservation is a floor-versus-round trap.** The chain computes `payoutStake.mul(payoutNumerator).div(den)` **per position id and
then sums** — integer floor, per leg, not once on the total:

| Holding | Payout vector | `redeemPositions` both legs | `mergePositions` then withdraw | Stranded |
|---|---|---|---|---|
| 7 YES, 7 NO | `[1,1]`, den 2 | `floor(7/2) + floor(7/2) = 6` | `min(7,7) = 7` | 1 unit |
| 10 of each of 4 outcomes | `[1,1,1,1]`, den 4 | `4 × floor(10/4) = 8` | `10` | 2 units |

Generalised: an N-outcome condition resolving `[1,1,…,1]` pays `N·floor(x/N) ≤ x` on a complete set of size `x`, and **the deficit stays in the
CTF contract permanently — there is no sweep function.** A mirror ledger computing the same quotient with round-half-up credits more than the
chain pays, invisibly until the first non-`[1,0]` payout vector. So: **merge complete sets before redeeming whenever the payout vector is not
`[1,0]`-shaped**, and assert `collateral_held ≥ Σ_i supply_i × num_i / den` continuously, with the chain's floor semantics. Finally,
**mark-to-market during a dispute is not mark-to-resolution**: in the Zelenskyy-suit market the YES price fell from $0.19 to $0.04 *during* the
dispute, which prices the **oracle vote**, not the event. Flag it in daily P&L, or you will book a governance outcome as a market move.
