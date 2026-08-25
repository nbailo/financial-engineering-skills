# Cancel against fill, and replace against fill, decided by the sequencer

> **Provenance**
> provider: Nasdaq, US equities · surface: OUCH 5.0 order entry, the cancel and replace interleavings ·
> version: OUCH 5.0, "Updated October, 2025", revision 1.05 dated 7 October 2025
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/TradingProducts/OUCH5.0.pdf ·
> https://www.sec.gov/litigation/admin/2013/34-69655.pdf
> verified: the OUCH 5.0 PDF was fetched and its text extracted and read directly on 2026-08-25, by two
> independent passes: "There is no “too late to cancel” message since by the time you received it, you would
> already have gotten the execution. Superfluous Cancel Order Messages are silently ignored"; and the
> new-order, replace and execution interleaving reproduced below.
> unverified: SEC Rel. 34-69655 ¶24 fn 4 was not re-read in this pass and is carried on its inline
> attribution.
> revalidate_when: Nasdaq publishes an OUCH revision touching cancel, replace or the acknowledgement model, or
> before the silence convention is read as any venue's answer other than Nasdaq's.

Each outcome below follows from where the concurrent command landed in the sequencer's total order, not
from a race inside the matcher. The forbidden outcome is an acknowledgement the engine then contradicts.

## Worked interleavings

The sequencer establishes one total order over commands; each outcome below follows from *where* the concurrent command landed
in it, not from a race inside the matcher.

**(a) Aggressor versus concurrent cancel.** Resting buy `O1` 200 @ 100 (member M); incoming sell 300 @ 100 (cmd `1001`); M's
cancel of `O1` (cmd `1002`). Each outcome is decided by the sequencer, not by a race inside the matcher.

| Sequenced order | Emitted event sequence |
|---|---|
| `1002` before `1001` | `5001 CancelAck(O1, leaves=0)` → `5002 OrderAccepted(sell 300)`; the sell rests, no execution. The ack is truthful |
| `1001` before `1002` | `5001 Execution(match=77, O1, 200 @ 100)` → `5002 Execution(match=77, aggressor, 200 @ 100)` → then whatever your protocol publishes for a cancel of an order that is already terminal. **Never a CancelAck** |

The forbidden third outcome is `CancelAck` followed by an execution on that order. A design that acks optimistically must be
able to **retract** the ack before the execution is emitted (NASDAQ ¶24 fn 4, where notifying members "was not discussed").

**What the wire carries instead is a rulebook entry, and at least two answers ship**: a typed reject naming the terminal state,
or silence. Nasdaq OUCH 5.0 chooses silence explicitly, read directly on 2026-08-25: "There is no “too late to cancel” message
since by the time you received it, you would already have gotten the execution. Superfluous Cancel Order Messages are silently
ignored." Note what that does and does not license. Internally the `(terminal, cancel)` pair is still enumerated, still refused
and still counted for an operator; the protocol decides only what leaves the process. An engine that omits the pair from its
table, rather than deciding to emit nothing for it, will eventually take the branch that mutates a terminal order.

**(b) Aggressor versus concurrent replace**, from the venue side (Nasdaq's own interleaving):

```
in : NewOrder(UserRefNum=7, qty=500)          out: Accepted(UserRefNum=7, qty=500)
in : Replace(orig=7, new=8, qty=500)          -- queued behind the aggressor
                                              out: Executed(UserRefNum=7, 100 @ px, match=91)
                                              out: Replaced(orig=7, new=8, leaves=400)
```

`leaves = chain_total(500) − cum_exec(100) − stp_decremented(0) = 400` under the chain-cumulative convention. The execution carries the **original**
`UserRefNum`; the replacement id appears only on `Replaced`, so an engine attributing the in-flight fill to the replacement
reports the wrong `leaves` next message. Note also that in OUCH's model the failure path of an amend is not "nothing happened".
