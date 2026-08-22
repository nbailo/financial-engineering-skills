# Tokyo Stock Exchange arrowhead — a failover that did not fire, and a halt that cut the wire while the matching engine kept matching (2020-10-01)

**Domain:** Cash equity exchange, matching engine, halt and resume | **Loss:** no direct trading loss published; the entire Japanese cash equity market failed to open and stayed halted for the full day — the first such outage since arrowhead launched. TSE's president resigned. | **Failure class:** Indeterminate outcome / partial failure (with change and configuration) | **Skill:** fin-matching-and-settlement

## What happened

At 07:04 a memory module failed in the control unit of NAS Device 1, one of two shared storage
devices arrowhead depends on. Automatic switchover to Device 2 did not occur. Manual switchover
succeeded at 09:26, but by then a second problem made restarting impossible: the trading halt
enacted at 08:54 had been implemented by shutting down the network to trading participants, while
the matching engine inside arrowhead had continued to run. Executions had accumulated inside the
system without being delivered. Participants held positions they had no way to learn about, and TSE
had no rules for resuming after a halt. At 11:45 the exchange halted for the day.

## Root cause, in code terms

**1. Failover correctness was a function of (setting × firmware version × failure mode), and the
vendor documentation was wrong about it.** arrowhead's NAS auto-switchover setting was **OFF**. The
Fujitsu manual stated that switchover happens **regardless of the NAS setting** — which was true for
generation 1 (control unit v7). From **arrowhead generation 2 (September 2015, control unit v8) the
product specification for OFF was "no switchover"**. The manual was never corrected. Fujitsu also
skipped pre-shipment verification "since the arrowhead settings were not the default settings" — so
the one test that would have exercised the actual deployed configuration was omitted *because* the
configuration was non-default, which is precisely when it needed exercising.

**2. The failover test injected a different fault class than the one being defended against.** TSE
did run switchover tests; they created a **mock network failure**. The real event was a **memory
module failure**, which is exactly the case the OFF setting did not cover. A failover test is a test
of a specific fault, and passing one fault class says nothing about another.

**3. The halt cut the transport and left the engine running.** From the JPX report:

> "orders that were received before 8:54 a.m. **had been matched and execution notifications had
> accumulated within arrowhead without being sent to trading participants**."

This is the finding that generalises furthest. Severing the network between a matching engine and
its participants does not stop the engine; it stops the participants finding out. Every execution
matched after 08:54 was economically real and epistemically invisible. Participants held positions
they could not see, could not hedge, and could not reconcile — which is the direct reason the market
could not reopen.

**4. There was no procedure for halting, and none for resuming.** TSE's report states that "we had
not prepared a contingency plan to halt trading in the case of the NAS becoming unavailable" and
notes "our lack of rules on how to handle trade resumption after a trading halt in the event of a
system failure." Halting is a state transition with economic consequences: somebody owns the orders
in flight, and somebody owns the fills that were matched and not reported. If that ownership is not
defined in advance, the only safe resumption is none.

## The invariant that was violated

```
# halt semantics
halt  =>  matching_engine.quiesced
      AND forall execution e matched before the halt:
              delivered(e)  OR  explicitly_voided(e)  OR  durably_queued_for_delivery(e)
NOT: halt := close(participant_network)

# resume semantics
resume requires a defined, tested disposition for:
    orders in flight, unacknowledged cancels, and matched-but-unreported executions

# failover verification
forall (setting, firmware_version, failure_mode) in the deployed matrix:
    failover_behaviour is verified EMPIRICALLY
NOT: failover_behaviour is asserted by vendor documentation

# and the fault injected must be the fault feared
test_failover(memory_failure) != test_failover(network_failure)
```

## Could an AI coding agent reviewing the diff have caught it?

**Partly — and the boundary here is unusually crisp.**

**What the agent cannot see:** that a vendor's manual is wrong. Nothing in the repository records
that Fujitsu's documentation described generation-1 behaviour while generation-2 hardware was
installed. That is a supplier-verification failure, and no amount of code review reaches it. It is
the clearest example in this catalogue of a defect that lives entirely outside the codebase.

**What the agent can see, in code and in test code:**

- **A failover test that injects the wrong fault class.** A test suite that exercises failover by
  dropping a network interface, when the failure being defended against is storage or memory, is a
  reviewable coverage gap. The reviewing question — "which faults does this suite actually inject,
  and which does the design claim to survive?" — is answerable from the test fixtures, and the
  mismatch is a finding.
- **A halt routine that closes connections without quiescing the engine.** This is the direct,
  visible defect. A `halt()` implementation whose body is "close the participant gateway" and whose
  body is *not* "stop accepting into the matching loop, drain, and either deliver or void what has
  already matched" is exactly a code-review finding. The signal is a halt function that touches the
  transport layer and nothing in the matching or delivery path.
- **A missing disposition for in-flight state.** If there is no code that answers "what happens to
  executions matched but not yet reported when we halt", the answer is "they sit there", which is
  what happened.
- **Failover behaviour asserted rather than tested.** A configuration comment or a constant with a
  name like `AUTO_SWITCHOVER_HANDLED_BY_VENDOR` and no test behind it is a claim without evidence.

## The rule

> **MUST — Halting a trading or value-processing system by severing its network must not leave the
> matching or execution engine running.** The halt must quiesce processing, and the design must
> define what happens to in-flight instructions and to executions that were matched and not
> reported.

> **MUST — There must be a documented, tested procedure for halting *and resuming*, including who
> owns the instructions in flight.**

> **MUST — Test failover against the specific failure mode you are protecting against** — kill the
> process, corrupt the disk, fail the memory — not a convenient proxy such as unplugging the
> network, and re-run those tests on every firmware or configuration change.

> **MUST — Verify failover behaviour empirically against the deployed setting and firmware version
> rather than trusting vendor documentation, and re-verify whenever either changes.**

> **MUST — A non-default configuration is a reason to run the verification, not a reason to skip
> it.**

## Sources

- **Tokyo Stock Exchange / JPX, *Report on the Cash Equity Trading System Failure on Oct. 1*,
  19 Oct 2020**, together with the *(Supplementary Material) NAS settings* —
  <https://www.jpx.co.jp/english/corporate/news/news-releases/0060/20201019-01.html> (PDFs at
  `.../b5b4pj000003r769-att/trading_system.pdf` and `trading_system2.pdf`). **Primary.**
  Establishes: the memory-module failure in the control unit of NAS Device 1 at 07:04; that
  automatic switchover did not fire because the NAS setting was OFF **and, from arrowhead
  generation 2 (September 2015, control unit v8), the product specification for OFF was "no
  switchover" while the vendor manual still said switchover happens regardless of the setting**;
  that Fujitsu skipped shipment testing because the arrowhead settings were not the default
  settings; that TSE's own failover test simulated a **network** failure rather than a memory
  failure (§2(1)); that after the participant network was shut down at 08:54 "**orders that were
  received before 8:54 a.m. had been matched and execution notifications had accumulated within
  arrowhead without being sent to trading participants**" (§1(2)); and that TSE "had not prepared a
  contingency plan to halt trading in the case of the NAS becoming unavailable" and lacked "rules on
  how to handle trade resumption after a trading halt in the event of a system failure" (§2(2)).
- **Companion case, same mechanism from the client side:** FINRA AWC 2020066971201, *Robinhood
  Financial LLC*, 30 June 2021 — ~166,000 orders left in an uncancellable "pending" state when the
  order-entry path went silent. Both incidents are the same invariant read from opposite ends of the
  wire.
