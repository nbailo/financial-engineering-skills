# ITCH executions, and which of them count as a print, a volume figure or an index input

> **Provenance**
> provider: Nasdaq for the message content, plus the UTP plan, FINRA and the CFTC for the eligibility rules
> around the feed
> surface: the execution-bearing message set, the deliberately constant fields, the order-entry seam, and
> which executions are published, counted in volume, counted toward an incentive or admitted to a benchmark
> version: TotalView-ITCH 5.0, whose revision log's most recent entry is dated April 28, 2023 · UTP Data Feed
> Services Specification Version 4.1, July 2026 · FINRA Rule 5210 Supplementary Material .02 · CFTC order of
> March 19, 2021
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf
> · https://www.utpplan.com/DOC/UtpBinaryOutputSpec.pdf
> · https://www.finra.org/rules-guidance/rulebooks/finra-rules/5210
> · https://www.cftc.gov/PressRoom/PressReleases/8369-21
> pinned: the two specifications were downloaded on 2026-08-25 and read as extracted text, not as a summary.
> verified: read in those documents on 2026-08-25: the full ITCH message-type inventory, the five
> execution-bearing messages and every sentence quoted from them, the `Printable` field, the Order Executed
> field table that carries no such field, the two constant fields with their effective dates, and the three
> Sale Condition rows with the columns they are rows of. The CFTC release was read the same day for its date,
> respondent, penalty and the sentence quoted.
> unverified: FINRA Rule 5210 could not be retrieved as source text on 2026-08-25, because finra.org refused a
> non-browser client with HTTP 403 and the one fetch that succeeded returned paraphrase rather than the rule's
> words; its substance is stated below without quotation marks and must be read in the rulebook before it is
> relied on. Also unverified: the OUCH quantity conventions, not re-read here; whether a bona fide self-trade
> is reported to a tape and counted in volume; and any index or benchmark methodology.
> revalidate_when: TotalView-ITCH publishes a version above 5.0 or a revision-log entry later than April 28,
> 2023 that touches a message quoted here; the UTP Sale Condition Matrix resolves either of the two rows it
> currently marks TBD (`E` Placeholder, `8` Placeholder For 611 Exempt) or adds a modifier; FINRA amends Rule
> 5210 Supplementary Material .02.

One execution is one fact, and four or five documents answer separately whether it is printed, counted in a
volume figure, counted toward a fee tier, or admitted to somebody else's benchmark. This file is the message
set those answers are computed from, what the documents disagree about, and why the answer has to be a filter
over stored raw prints rather than a flag written onto them at match time. Read it when the change decides
what is printed, what is counted, or what a statistic you publish is computed from.

## Contents

- The obligation: the raw print is one fact and eligibility is several filters over it
- ITCH content: the complete trade-print set, named, and why the printable flag is not the filter
- Which prints count: the raw fact, and the four filters over it
- Deliberately-constant ITCH fields, their values and their effective dates
- OUCH: why an order-entry protocol appears in a market-data file at all
- A published statistic is a method, and the method is part of the feed

---

## The obligation

**The raw print is one fact; eligibility is several filters over it, answered by different documents.**
Whether an execution is published as a print, whether it counts toward the volume figure you report, whether
it updates a last, high or low, whether it counts toward a fee tier, rebate or other incentive, and whether a
downstream index or settlement benchmark admits it are separate answers, decided in different places. Keep the
raw print unmutated and derive each figure from it by its own filter, so a filter that changes is a
recomputation rather than a fact you no longer hold, and publish which filter feeds which computation. Trade
prints are not book events, and counting them as book updates double-counts depth.

## ITCH content: the complete trade-print set, named

TotalView-ITCH 5.0 defines 23 message types, identified by the letters `S`, `R`, `H`, `Y`, `L`, `V`, `W`, `K`,
`J`, `h`, `A`, `F`, `E`, `C`, `X`, `D`, `U`, `P`, `Q`, `B`, `I`, `N` and `O`. Exactly five of them carry,
qualify or retract an execution, and a volume computation that misses one of the five is wrong by whatever
that message type carried that day.

| Message | Type | Book event | Volume |
|---|---|---|---|
| Order Executed | `E` | yes | yes |
| Order Executed With Price | `C` | yes | only when `Printable` is `"Y"` |
| Trade Message, non-cross | `P` | no | yes |
| Cross Trade | `Q` | no | yes, the bulk cross volume, and it "may show the shares as zero" |
| Broken Trade | `B` | no | removes a quantity already printed |

The other eighteen types carry no execution at all. `A` and `F` add an order to the book, with and without
MPID attribution; `X`, `D` and `U` cancel, delete and replace one; and `S`, `R`, `H`, `Y`, `L`, `V`, `W`, `K`,
`J`, `h`, `I`, `N` and `O` are system, reference, halt, imbalance and listing messages. The specification
states the join across the five itself: "By combining the executions from both types of Order Executed
Messages and the Trade Message, it is possible to build a complete view of all non-cross executions that
happen on Nasdaq. Cross execution information is available in one bulk print per symbol via the Cross Trade
Message."

Four consequences a publisher owns.

- **The printable flag is on one message type, not on the feed.** Only Order Executed With Price carries
  `Printable`, described as indicating "if the execution should be reflected on time and sales displays and
  volume calculations". The Order Executed field table in the specification read here carries no such field.
  A volume filter keyed on `Printable` alone therefore discards every execution against a displayed order,
  silently, and the size of what it discards is the size of the displayed book's trading.
- **Non-printable means covered later, not cancelled.** "If the execution is marked as non-printed, it means
  that the shares will be included into a later bulk print (e.g., in the case of cross executions)", and the
  instruction that follows is to ignore those messages "to prevent double counting". The Cross Trade section
  says the same thing from the other end: "To avoid double counting of cross volume, firms should not include
  transactions marked as non-printable in time-and-sales displays or market statistic calculations." The bulk
  print and the executions it covers are the same shares, and summing both is the classic doubling. It is
  invisible in a quiet test capture, because it needs a cross to appear at all.
- **One message, two opposite answers.** Trade messages "should be included in Nasdaq time-and-sales displays
  as well as volume and other market statistics", while "since Trade Messages do not affect the book, however,
  they may be ignored by firms just looking to build and track the Nasdaq execution system display". That is
  the two-filter rule stated by the venue in one sentence, and it is the sentence your own specification has
  to contain an equivalent of.
- **Volume is not monotonic.** A Broken Trade message names the `Match Number` of "a previously transmitted
  Order Executed Message, Order Executed With Price Message, or Trade Message", and the break is final: "once
  a trade is broken, it cannot be reinstated". Anything downstream that only ever adds is wrong from the first
  break of the day.

## Which prints count: the raw fact, and the four filters over it

The execution is one fact. Publishing it as a print, counting it in an official volume figure, counting it
toward a fee tier or rebate, and admitting it to a downstream benchmark are four filters applied to that fact,
decided in four places. A publisher that collapses them into one number has already chosen an answer without
recording which one, and has usually done it by mutating the print rather than by filtering a set. Store the
raw print, derive each figure from it, and publish which filter produced which figure.

**Your own specification** decides what leaves the process and which of those messages you count. On ITCH that
is the five-message set above plus the `Printable` flag, and nothing else.

**The tape or plan rulebook** decides what the official consolidated record counts, and it does not answer
with one bit. The UTP Data Feed Services Specification carries a Sale Condition Matrix whose columns are
separate answers about the same print: consolidated Update High/Low, consolidated Update Last, market-centre
Update High/Low, market-centre Update Last, and Update Volume. Three rows are enough to show the shape.

| Modifier | Condition | Consolidated high/low, last | Market centre high/low, last | Volume |
|---|---|---|---|---|
| `W` | Average Price Trade | No, No | No, No | Yes |
| `I` | Odd Lot Trade | No, No | No, No | Yes |
| `M` | Market Center Official Close | No, No | Yes, Yes | No |

So "did this trade count" has five answers, not one, and two of these rows count toward volume while moving no
price at all. A publisher reporting a single volume figure is reporting one column of that matrix and owes the
consumer a statement of which.

**Your incentive schedule** decides what counts toward a fee tier, a rebate or a volume programme, and it is
a document you write, so nothing outside your process will contradict it. That is exactly why it has to be a
filter over the raw prints rather than a flag set on them at match time: the day the schedule changes, a
filter is recomputed and a flag is a fact you no longer hold.

**The counterparty rulebook** decides whether a match between accounts under one beneficial owner is a
legitimate trade, and the answer is not universally no. FINRA Rule 5210 Supplementary Material .02 addresses
self-trades, meaning transactions resulting from the unintentional interaction of orders originating from the
same firm that involve no change in beneficial ownership, and treats them as generally bona fide for purposes
of Rule 5210, including where the orders originate from unrelated algorithms or separate and distinct trading
strategies within the same firm. That substance was confirmed on 2026-08-25 through a fetch that returned
paraphrase rather than the rule's words, so read the rulebook before relying on it; it is already enough to
defeat a universal exclusion. Whether a bona fide self-trade is then reported and counted is a trade-reporting
question in the same rulebook family, and it has to be read rather than assumed; it was not read in this pass.

What is sanctioned is the other case. The CFTC's order of March 19, 2021 against Coinbase Inc., carrying a
6.5M USD penalty, concerned false, misleading or inaccurate reporting and wash trading, and the commission's
release records that transactional information of this type "is used by market participants for price
discovery related to trading or owning digital assets, and potentially resulted in a perceived volume and
level of liquidity of digital assets, including Bitcoin, that was false, misleading, or inaccurate."

Do not implement "self-matches are excluded from volume" as a universal, because it is not one. Name the rule
your venue is subject to, apply it in exactly one place, and publish which set each figure was computed from.
The fifth document, a third-party index or settlement methodology, is one you do not write at all; where your
figure feeds it, your filter errors leave with it.

## Deliberately-constant fields

Nasdaq TotalView-ITCH 5.0 carries two fields on the Trade (non-cross) message that are constant by design, and
states both in the specification, including the date each became constant:

| Field | Constant value | Effective | What a consumer wrongly infers |
|---|---|---|---|
| `Order Reference Number` | `0` | December 6, 2010 | that the print can be linked to a resting order |
| `Buy/Sell Indicator` | `"B"` | 07/14/2014 | aggressor side: the field is `"B"` "regardless of the resting side" |

The maintenance hazard is one-directional and permanent. Once a constant has shipped, consumers have written
branches whose other arm is dead code, and transaction-cost reports built on a value that means nothing. You
cannot un-constant the field on the same feed version: the day it carries real data, every consumer that
special-cased it silently produces different output, and nothing in the message says the semantics changed.

Two rules follow. State the constant and its effective date, which Nasdaq does. And populate it from a named
encoder constant with a test asserting constancy, rather than from a live value that happens to be constant
today. A field constant by accident becomes variable by accident.

## OUCH

OUCH is Nasdaq's order-entry protocol, not a market-data feed, and it appears here for two reasons. The first
is the shared timestamp convention above, which is a property of the family rather than of one protocol. The
second is the seam: a venue that speaks OUCH inbound and ITCH outbound has one event, the execution, that must
appear on both, and the quantity conventions differ between the order-entry and market-data views. Where a
change spans that seam, the order-entry half belongs to the matching-engine skill and only the published half
is governed here. Do not derive a market-data rule from an order-entry message layout, and do not assume a
quantity field means the same thing on both sides without checking the specification for each.

## A published statistic is a method

**A statistic you publish is a method, and the method is part of the feed.**
Name which number is the record for each calculation, and separate the number that reports from the number
that decides. Publish the input set, the eligibility filter, the calculation and the effective date: a consumer
who cannot recompute your figure cannot tell it moved for a reason that was not trading, and neither can you,
because the figure comes back as evidence about itself. Where it feeds somebody else's index or settlement,
your filter errors leave with it. Specialises *authority*.
