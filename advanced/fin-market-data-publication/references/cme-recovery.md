# CME MDP 3.0: the snapshot join, and recovery that terminates

> **Provenance**
> provider: CME Group · surface: Market Data Platform (MDP) 3.0 as documented for client systems
> version: MDP 3.0, read as pages of the CME Group Client Systems Wiki, which carries no edition number. Page
> id, version and last modified: MBP and MBOFD Market Recovery 457672425 v4 2025-10-01 · Market Data Snapshot
> Full Recovery 457736274 v3 2026-02-19 · Book Recovery Methods 457705188 v3 2025-01-10.
> verified_at: 2026-08-25
> sources: https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457672425
> · .../457736274 · .../457705188
> pinned: each page was read through the wiki's content API on 2026-08-25, which returns its version and
> last-modified timestamp in the same response as the body.
> verified: every sentence in quotation marks below was read on one of those three pages that day, including
> the tag 369 definition, the six recovery steps and both natural-refresh warnings.
> unverified: nothing quoted here. Two sentences the previous edition quoted were deleted rather than kept,
> because neither could be found on this wiki in this pass: one assuming every book on a channel is stale
> after a packet gap, one requiring the latest incremental to be processed before a book is valid.
> www.cmegroup.com still refuses a non-browser client with HTTP 403, so the wiki is where a recheck starts.
> revalidate_when: CME publishes an MDP version above 3.0; the recovery pages stop naming tag 369 as the join
> point; a cited page moves past the version recorded above.

The most completely specified public example of a recovery path that terminates: a snapshot naming its join
point in the incremental stream, and a statement that the cheap opportunistic mechanism is not a substitute
for it. Read it when a consumer joining mid stream has to know whether it is caught up.

## Snapshot and incremental

A snapshot is the book as of a stated point in the incremental stream, and without that point it is unusable,
because the consumer cannot know which buffered incrementals it already contains. Tag `369` is defined as the
"Sequence number of the last Incremental feed packet processed. This value is used to synchronize the snapshot
loop with the real-time feed", and the recovery page repeats it as a join: "the tag 369-LastMsgSeqNumProcessed
value on the Snapshot message corresponds to the packet sequence number on the Incremental feed".

The consumer algorithm your snapshot has to support, from that page:

1. Identify the channels you are out of sync on, then listen to the incremental and **queue** it.
2. Join the snapshot loop. "The order of each Snapshot message iteration is not guaranteed; client systems
   must process one full iteration of a Market Recovery Snapshot message starting at sequence number 1 to
   ensure full recovery." A partial iteration is not a recovery.
3. Per instrument, compare tag `369` against the queued incremental packet sequence numbers.
4. If a `SecurityID` appears in both, compare tag `60-TransactTime` on each side. "The instrument with the
   unequal 60-TransactTime must be recovered via the next market recovery cycle or optional concurrent natural
   refresh processing."
5. "Drop all cached Incremental feed updates with a packet sequence number < 369-LastMsgSeqNumProcessed."
   Apply the remainder in order.
6. Books go live per instrument: "Once a book is recovered, client systems can resume normal processing for
   that instrument even if other books are still being recovered."

## Natural refresh, and why it is not a terminating mechanism

MDP offers a cheaper way back to a correct book: rebuild it from the live incremental without prior state.
The book recovery methods page is explicit about the cost, "Prior to beginning a natural refresh, the entire
book should be emptied. Natural refresh assumes no prior knowledge of book state", and about the limit, in a
warning of its own: "Natural Refresh is not guaranteed and should not be considered a definitive substitute
for recovering lost data. Natural Refresh should only be used in conjunction with Market Recovery."

That is the sentence to copy. A mechanism that rebuilds state opportunistically from whatever the live stream
happens to send has no terminating condition: a quiet instrument never refreshes, and nothing tells the
consumer whether the book they hold is complete. It shortens the wait for the mechanisms that do terminate,
and publishing it as the answer to a gap leaves consumers waiting on an event that may never arrive.
