# The Canonical Rule Spine

**Status:** final. This file is the single source of truth for what the seven `fin-` skills contain.
`docs/architecture.md` decides the taxonomy; this file decides the rules, their wording, their placement,
and their evidence. Where the two disagree about a rule's content, this file wins; where they disagree about
which skill owns it, `architecture.md` wins.

**Audience:** the skill authors who will write `skills/fin-*/SKILL.md`, and anyone auditing why a rule is in
or out.

---

## 1. How these rules were selected

Two questions decide whether a rule is in this file: **is it true**, and **does stating it change the code**.

**Truth comes from primary documents.** Every rule below rests on at least one of: a vendor's own API
documentation (error codes, filter predicates, retention windows, field semantics), a protocol specification
(FIX, Nasdaq OUCH/ITCH, MoldUDP64, CME MDP 3.0, ISO 20022, EIP/ERC), a regulator's own text (17 C.F.R.
§ 240.15c3-5, SEC administrative proceedings by release number, FCA Final Notices, Federal Reserve accounting
manuals), a standard (ISO 4217, IEEE 754), a named and dated incident with a published account, or the source
of a mature open-source financial system read at a pinned commit with `repo/path:LINE` given. Where a fact
could not be traced to one of those, it was cut rather than softened (see §1.2).

**Attention comes from what actually gets left out.** A rule that a competent engineer applies without being
told earns nothing by being written down; it costs tokens, and worse, it pushes the rules that *do* get
skipped further from the top of the context window. So the set is deliberately small, and it deliberately
omits a long list of correct, well-known practices: `Decimal` for money, tick and step rounding,
`MIN_NOTIONAL`, webhook signature verification, event-id dedupe, waiting for confirmations, weighted-average
entry price, deterministic lock ordering, explicit isolation levels. §4 lists them all, with the reason each
one is out. Where one of those practices has a residue that *is* routinely missed (the ORM boundary under
`Decimal`, the fee gross-up under tick rounding, the unresolvable event under webhook dedupe) the residue
ships as its own rule and the well-known half does not.

The two questions can disagree. A rule can be severe and still not worth a section: MC1 (float money) leads
every finance style guide on earth and sits here at **reference** placement, because the failure it describes
survives today mainly at boundaries a style guide never reaches: an ORM that hands back `float64`, a
protobuf schema, an analytics export. Conversely a rule can name no famous incident and still belong on the
front page: the prose TODO, naming the correct control accurately and writing a comment instead of
implementing it, appears in no postmortem, because postmortems describe what broke rather than what was
never built. It is the rule *implemented, not described*, stated in full in `fin-money-core` and specialised
by name in every other skill.

### 1.1 What each rule block carries

Every rule below states its text, its strength, its owning skill and its placement, followed by a
`- **source:**` line naming the primary documents it rests on, and, where one exists, a worked example.
Where a rule is kept despite being widely known, the source line says what part of it is *not* widely applied
and why that part is the rule.

### 1.2 Evidence hygiene: verify or cut

Every venue fact, error code, retention window and field name in this document was checked against the
primary document. Where a fact could not be verified it was **cut**, not hedged. A front-placed rule may not
carry an unsourced factual payload: a wrong error code or endpoint path destroys the reader's trust in every
rule around it, and the mechanism is almost always statable without it. What verify-or-cut removed or
corrected:

| Claim in the draft spine | Disposition |
|---|---|
| `GET /fapi/v2/positionRisk`, `GET /fapi/v2/account`, "the income endpoint", `ORDER_TRADE_UPDATE.o.rp` | **CUT.** No `fapi/v2` or `fapi/v3` path and no `o.rp` field could be verified against Binance's published documentation. EX12 is restated venue-agnostically over the mechanism Binance *does* document: per-endpoint Matching-Engine/Memory/Database data sources, and REST↔WS drift. |
| XRP `DestinationTag` = uint32; Stellar memo types | **VERIFIED.** XRPL docs: "formatted as a 32-bit unsigned integer"; the `RequireDest` setting rejects payments lacking one. Stellar docs: `MEMO_TEXT` ≤28 bytes, `MEMO_ID` 64-bit unsigned integer, `MEMO_HASH` 32-byte hash, `MEMO_RETURN` 32-byte hash. Cosmos/TON/EOS claims **cut** as unverified. |
| "Alchemy 10 blocks free tier / 10,000 PAYG" | **CORRECTED.** Alchemy's published table: free tier 10 blocks on every chain; PAYG **unlimited on major chains**, 1,000 on Monad testnet, 1,000 on Berachain, 10,000 on all others. The cap is tier- *and* chain-specific; ON1 now says read it from config and prove completeness rather than quoting one number. |
| Nacha "R10/R29 60 calendar days" | **R29 CUT.** R10 is returnable for 60 calendar days after settlement; administrative returns (R01 NSF, R02–R04, R09) are due within 2 banking days. Under Nacha, R29 is a corporate return on a 2-banking-day clock, so the pairing was inverted. |
| Stripe "a refund can move to `failed` up to 30 days later" | **VERIFIED and scoped.** Stripe: "the bank returns the refunded amount to us and we add it back to your Stripe account balance. **This process can take up to 30 days from the post date.**" It is a property of the return-of-funds process, not a flat fact about all refunds. |
| Binance `autoclose-` / `adl_autoclose` / Bybit empty `orderLinkId` | **RE-ATTRIBUTED.** These markers are documented by **NautilusTrader's** Binance and Bybit integration docs, not by Binance or Bybit. EX10 now says so. |
| "Stripe's four distinct scales per currency" | **SPLIT.** Stripe documents charge-vs-payout divergence (HUF/TWD payable in multiples of 100; ISK/UGX must end `00`); **Kraken** separately documents 10 calculation and 5 display decimals for BTC. Two vendors, not one. |
| "US federal tax is half-up on the total only" | **CUT.** Not sourced well enough to ship, and an oversimplification anyone who has implemented it will correct. |
| Compound "could not stop an ~$80M mis-distribution" | **CORRECTED.** **~168,000 COMP (~$50M) actually claimed**; ~$80–83M is the bounded worst case. The bound is not the event. |
| Knight "97 emails, 89 minutes before the open, to a mailing list" | **CORRECTED.** 89 minutes is derived arithmetic (8:01→9:30), not text. SEC Rel. 34-70694 says "a group of Knight personnel", not a mailing list. Footnote 6 records that the orders generating those emails "were distinct from the 212 incoming parent orders": the alerts were not about the incident orders, which is the general case for pre-open canaries and changes the lesson. |
| Hyperliquid JELLY under an oracle-hygiene rule | **SCOPED.** It is a perp-DEX mark-manipulation event; it supports only ON15's single-venue clause, and Compound's DAI/Coinbase-Pro feed (~$89M liquidated) is the better citation for that clause. |
| EX1 and MC1 labelled "verbatim" | **FIXED.** EX1 had dropped two venue rows (Kraken FIX; the retention-bound clause), now restored. MC1 was edited rather than quoted, so it is relabelled "adapted from". |

Unverified items that must **never** be asserted as fact in shipped prose: Adyen's and PayPal's
same-key-different-body behaviour (inference, not a quoted guarantee); Bybit `orderLinkId` retention window;
Coinbase Advanced Trade idempotency retention; Hyperliquid `cloid` collision behaviour; whether any provider
*silently* truncates `eth_getLogs` (the verified failure is an explicit error; the rule covers both); the
EPC 15-business-day Recall answer window (from an abstract, not the rulebook PDF); EUR-Lex primary text for
C-302/07; FIX SBE via fixtrading.org. Where a venue fact is unverified, the rule is written to be safe
either way.

---

## 2. The seven cross-cutting rules

These seven apply in every domain. `fin-money-core` states each one in full, under the name given here, in
its `## Core rules` section. The other six skills specialise them in their own vocabulary and cite them by
name. Nothing here depends on `AGENTS.md`: the routing block states no rule, and a skill installed on its own
carries every rule it needs. The numbered ids these rules used to carry are retired, and `architecture.md`
§0.2 records the order for anyone reading older material.

**Seven rules, not thirteen.** The draft carried thirteen. Ten of them restated a skill rule, and four
(F10 to F13) were domain-specific, so a ledger-only session paid for exchange and payments rules it could
never use. The set below is the gate, the two discipline rules that dominate everything else, and the four
genuinely cross-domain mechanisms. The original size argument was a budget one: this text sat in every
conversation whether or not it was relevant, which capped it at roughly 200 words of standing rules. That
budget no longer binds, because each rule now lives in the skill the change routed to. The discipline
survives for the other reason. Past some rule count, added rules stop being followed and start crowding out
the ones that were.

**Duplication, stated plainly.** *Durable intent before the external effect*, *arrival order is not
occurrence order*, *proven coverage before the cursor advances* and *reconciliation runs in production* are
the core halves of MC4+MC6+MC15, MC10, MC9 and VF1. Each is stated in `fin-money-core` and again, in domain
vocabulary, in every skill that needs it, because every skill must be fully useful alone
(`architecture.md` D3). The earlier claim that "nothing else in the suite is duplicated" is retracted; see
§6.5.

---

### The economic-diff gate

Gate first, and default to ON. Before anything else, decide whether this change is economic. It is economic
by default if any changed path matches the repo's money paths, imports a payment, exchange, chain or ledger
client, or touches a symbol matching
`balance|amount|price|qty|order|refund|payout|transfer|ledger|posting|settle|withdraw`. Otherwise answer
AMOUNT, EFFECT, AUTHORITY, REPLAY, ROLLOUT from the change alone. All five NO and no match: emit
`ECONOMIC-DIFF: none` and review normally. Otherwise emit `ECONOMIC-DIFF: <which>` plus
`Financial tier: T<n> (inferred from: <signal>)` and apply the rules of the matching skill. The gate's job is
to **exempt**, not to admit.

*Why: without a cheap first gate the whole suite is either always-on or never-on. A gate whose cheapest
compliant emission is "none" is a single discretionary decision, made once and early, by the party with the
incentive to decline, so the gate must default to ON and exempt only on a mechanical predicate.*

### Implemented, not described

A named risk is implemented, or the process refuses to start. In a money path, a comment, a TODO, a design
note, a "worth adding", a defined-but-uncalled function, or a `...` stub describing a missing control is the
same defect as the missing control. Every control you name carries its evidence on the `controls:` line of
the `FINANCIAL CHECK`: a real `file:line`, or an explicit `UNRESOLVED: <control> (<why>)`. At T2 and above
the same line also carries the test name, `<control> -> <file:line> · <test name>`, and a control with no
`file:line` fails the run. If you will not implement it, make the path uncallable:
`raise NotImplementedError` **on a path that is actually reached**, not inside a function nothing calls.

*Why: naming the risk accurately and then writing a comment instead of the control is the single most
common way a money-path defect ships. An omitted element is added by a required slot in a template the
author is already filling; it is not added by a reminder somewhere else.*

### A comment is a claim

Every claim in a comment is checked against the code in the same pass. Read the design-notes and docstring
section as a list of claims. For each property asserted ("the flush guarantees the row exists", "the
monotonic guard makes gaps impossible", "sequence numbers are consumed on rejects so there are no gaps"),
either point at the test that proves it or delete the sentence.

*Why: the asserted invariant is very often exactly where the bug lives, and the assertion is what lets it
survive review.*

### Durable intent before the external effect

An ambiguous external call has three phases, and the first one COMMITs. Mint the idempotency key from the
intent instance, from a value that survives `ROLLBACK`. Commit the intent row carrying that key (`flush()`
inside an open transaction is not persistence), then make the call, then record the outcome. A timeout,
socket close, 5XX, 429, `-1006` or `-1007` is UNKNOWN, never "did not happen": leave the intent committed,
query the counterparty for the identity you sent, and never resubmit. No `session.begin()` /
`engine.begin()` / `@transaction.atomic` may lexically enclose the external call.

*Why: rolling back on the exact timeout the intent row exists for is the classic double-pay. BIGSERIAL keeps
advancing across the rollback, so the retry mints a new key and pays twice, and code that survives the
timeout by leaning on a venue-side uniqueness guarantee is relying on something most venues do not give.*

### Arrival order is not occurrence order

A pushed event is a notification whose arrival order you do not control. Two guards, both required.
**Legality:** enumerate the legal `(state, event)` pairs and reject everything else with an explicit error;
do not silently ignore. A terminal state accepts exactly the events by which the venue corrects a fact you
already booked (a late fill, a fill void) and nothing else; it is never re-opened by a *status* message.
**Version:** the watermark is keyed on the entity id, stored independently of the live object, and the guard
**is** the write: `UPDATE watermarks SET v=:v WHERE id=:id AND v<:v`, proceed only on rowcount 1, in the same
transaction as the effect. Then re-read the object from its authority (amount, status and attribution)
before any value-moving decision.

*Why: this one root cause produces a corrupted perp entry price, a refund regressed to pending, and phantom
resting orders. Re-reading the object from its authority before acting is what closes all three.*

### Proven coverage before the cursor advances

A watermark advances only past a range you verifiably covered. Advance a cursor, watermark or high-water
mark only inside the same conditional and the same transaction that covered that range. An error, a provider
range rejection, a result count at the documented cap, or a truncated page is a hole, not an empty result. A
branch that skips the work skips the advance.

*Why: permanent silent under-crediting. A deposit vanishes with no error and no log line, so nothing ever
surfaces it except a customer complaint or a reconciliation you have not written yet.*

### Reconciliation runs in production

The reconciliation runs in production, or it does not exist. Name the external authority and the join key for
every economic quantity you report, and ship the comparison as a scheduled entrypoint that reads through a
path independent of the writer. The alert destination is a config key with **no default** that raises at
import if unset. An invariant that exists as SQL in a comment, a docstring, or a "worth running as a cron"
note counts as absent.

*Why: it is the only control that catches the failures of every other rule, and it is the one most often
written down and never scheduled.*

---

## 3. The canonical rule table

**89 rules. Every rule is owned by exactly one skill.** Strength is `MUST` unless stated. Placement is one of:

| Placement | Meaning, against the mechanism that decides it |
|---|---|
| `front` | Inside the **first ~5,000 tokens** of `SKILL.md`. Claude Code re-attaches only the first 5,000 tokens of each skill after auto-compaction, within a 25,000-token combined budget filled newest-first; a rule past that point can vanish mid-task with no signal. Budget: **≤120 words per front rule, ≤2,000 tokens of front rule text per skill.** |
| `body` | Later in `SKILL.md`, inside the 500-line / 5,000-token ceiling. Survives normally; may be lost to compaction. |
| `reference` | `references/<topic>.md`, one level deep, reached by a dispatch row keyed on an observable predicate (a venue string, a library import, a rail name). |

Front counts: core 8 · exchange 10 · payments 6 · onchain 8 · ledger 5 · matching 6 · verification 7 = **50
front rules ≈ 8,000 tokens across seven skills**, ~1,150 tokens of front rule text per skill. This fits. The
constraint that actually exists is the 5,000-token compaction window, and it is stated in tokens here so the
arithmetic can be checked rather than argued about in lines.

---

### 3.1 `fin-money-core`: 15 rules (8 front)

| id | strength | placement |
|---|---|---|
| MC1 | MUST | reference |
| MC2 | MUST | front |
| MC3 | MUST | body |
| MC4 | MUST | front |
| MC5 | MUST | body |
| MC6 | MUST | front |
| MC8 | MUST | body |
| MC9 | MUST | front |
| MC10 | MUST | front |
| MC12 | MUST | front |
| MC13 | MUST | front |
| MC14 | MUST | body |
| MC15 | MUST | front |
| MC16 | MUST | reference |
| MC17 | SHOULD | reference |

---

**MC1 · MUST · reference: representation**

Represent every value by what it is, not by where it lives. A value a counterparty can demand (balance,
posting, fee, tax, invoice line, settlement, payout, token amount, or an order price/quantity compared
against venue filters) is an **obligation**, held for its whole life as integer minor units, a scaled
integer with a declared exponent, or an arbitrary-precision decimal constructed only from strings or
integers; persisted as `NUMERIC(p,s)` or an integer; transported as a string, `google.type.Money`, or
`{scaled_int, scale}`. A value nobody can demand (greeks, implied vol, VaR, Monte Carlo paths, backtest
statistics, ML features, chart coordinates) is an **estimate**, and binary floating point is the correct
type for it. A float may be read **only as the argument of the single named, tested
`quantize(value, scale, mode)` call** that turns an estimate into an obligation; the constructor is the
artifact, not the intent. Every inexact decimal operation (division, `**`, any conversion) executes inside a
`localcontext()` with a declared `prec` and `traps[Inexact]` set, `Decimal("1")/Decimal("3")*Decimal("3")`
is not 1 at `prec=28` with `Inexact` untrapped, and MC1 would otherwise govern construction and storage while
saying nothing about division. Where a counterparty's published schema forces a float on the wire (Deribit
`price`/`amount` are JSON `number`; IBKR `Order.LmtPrice` is `double`), the float is a **transport encoding
only**: compute and store exact, convert once at the boundary, assert the encoding round-trips, and re-derive
the authoritative record from the venue's own decimal/string echo. **Assert the returned language-level type
in a test**: a correct `NUMERIC(18,8)` column is undone by an ORM that hands back `float64`.

- **source:** ISO 4217 exponent semantics; Deribit and IBKR published schemas (Deribit `price`/`amount` are
  JSON `number`, IBKR `Order.LmtPrice` is `double`). Read in production code: **freqtrade stores every money
  field as SQL `Float`** (`trade_model.py:95-117`), aggregating in `ccxt.Precise` string math and casting back
  to `float` for storage, which is *defensible* there, because freqtrade is a book of record for nobody and
  can re-derive every aggregate from the order rows. That carve-out is why MC1's boundary is "is this an
  authority for a third party", not "is this finance".
- **example:** `Decimal(0.1)` vs `Decimal("0.1")`; a `NUMERIC(18,8)` column read back through psycopg into a
  Python `float`.

**MC2 · MUST · front: currency identity**

Every stored amount has a sibling currency/asset identifier in the same row, struct or type, and every
comparison, sum and equality check reads it. A schema with `amount_cents` and no `currency` column is
silently wrong for JPY, KRW, VND, CLP, ISK and UGX (exponent 0), for BHD, KWD, JOD, OMR and TND (exponent 3)
and for CLF (exponent 4), and cannot validate the currency on an inbound webhook or fill.

- **source:** ISO 4217 minor-unit exponent tables, exponent 0 (JPY, KRW, VND, CLP, ISK, UGX), exponent 3
  (KWD, BHD, JOD, OMR, TND), exponent 4 (CLF), cross-checked against Stripe's published zero-decimal list.
  Separately, and not to be merged into one claim: **Stripe** documents charge-vs-payout divergence (HUF/TWD
  chargeable at 2 decimals but payable only in multiples of 100; ISK/UGX must end in `00`), and **Kraken**
  documents 10 calculation decimals against 5 display decimals for BTC. Two vendors, two distinct scale
  hazards.

**MC3 · MUST · body: rounding direction, scale, and residue**

Choose rounding direction per operation from the operation's category, never as a global default, and post
the residue to a named account. **(1)** If a statute, regulation, scheme rule or contract names the mode,
level or day-count, copy it and store it as per-jurisdiction/per-instrument configuration, never as a
constant: EU Member States fix VAT rounding and round-up may be mandatory, and euro conversion is legally
half-up. **(2)** Otherwise, for any exchange between two representations of the same value where the
counterparty chooses when and how often (shares↔assets, LP tokens↔reserves, base↔quote, points↔cash):
**floor the amount the system pays out, ceil the amount the system collects, per leg**: one rounding helper
applied to both legs is the Balancer V2 bug. **Direction without a scale is a no-op:** name the scale at
every call site; `ceil` at 18 decimals rounds nothing. Also check the denominator: if it can reach 0 or 1, or
be inflated by a transfer that bypasses the accounting entrypoint, direction alone is insufficient: add
virtual shares/assets or seed-and-burn. **(3)** When splitting one exact total into parts, direction is
irrelevant and conservation is mandatory: largest-remainder in integer minor units, deterministic tie-break,
`assert Σ parts == total`. **Property test, stated over multiple actors:** for an adversarially ordered
sequence of operations by *different* principals, `Σ outputs ≤ Σ inputs` per asset and no user-initiated
round trip returns more than it put in. The per-actor form survives an A-deposits / B-withdraws extraction.

- **source:** EIP-4626 and OpenZeppelin's direction table; Balancer V2 ComposableStablePool, 3 Nov 2025,
  >$120M; Cetus overflow mask + `>` vs `>=`; the empty-market class (Hundred Finance → Midas → Onyx). For the
  statute clause: CJEU C-302/07 (*J D Wetherspoon*) on Member States fixing VAT rounding, and Council
  Regulation (EC) No 1103/97 Arts. 4–5, under which euro conversion rounding is legally half-up. For clause 3:
  Fowler's `allocate` and Dinero's `allocate`, largest-remainder with an asserted total.

**MC4 · MUST · front: idempotency key derivation**

Mint the idempotency key from the identity of the **intent instance**, a value unique per decision-to-act
and byte-identical across every retry of that decision, never from a hash of the request body. Mint it in
the process where the decision is made and commit it, together with the exact serialized request bytes and
the target `(provider, endpoint/region, credential)`, in the same durable transaction that records the
intent, **strictly before the first byte is sent**; on every retry replay the stored bytes verbatim under the
same key. Testable corollaries: `key_for(intent)` is stable across n calls and differs for distinct intents
with byte-identical payloads; no `uuid4()`/`ulid()` sits inside any function a retry loop can re-enter; the
key derives from a value that **survives `ROLLBACK`**; a Postgres BIGSERIAL does not, so a key built from an
uncommitted row id yields `N+1` on the retry and buys a second real refund; `(tenant, key)` has a unique
index and there is no `SELECT`-then-`INSERT`; the unique-violation path is caught and resolved to the
winner's stored result, never surfaced as a raw `UniqueViolation`.

- **source:** Brandur, *Implementing Stripe-like Idempotency Keys in Postgres*, atomic phases and recovery
  points, *"atomic phases should be safely committed before initiating any foreign state mutation"*;
  TigerBeetle, *Reliable transaction submission*; IETF `draft-ietf-httpapi-idempotency-key-header-07`. In
  production code: stripe-node mints the key **above** the retry loop, while CCXT's auto-generated client
  order ID is **fresh per call**, which covers the SDK's internal retry and nothing else.
- **example:** WRONG: `db.add(refund); db.flush(); key = f"order-{order.id}-refund-{refund.id}"; ... except
  StripeError: db.rollback()`. RIGHT: `key = uuid4()` at intent formation, `db.add(Attempt(key=key,
  body=body)); db.commit()`, then call.

**MC5 · MUST · body: idempotency key enforcement**

The idempotency key parameter is **required at the type level**, not `Optional[str] = None` with enforcement
deferred to prose about the API layer. **When you own the server or the resource**, the server compares the
stored row's economically significant fields (from, to, amount, currency) to the incoming request; a key match
with a field mismatch returns an error that neither executes the request nor replays the stored response, and
the stored fingerprint is a salted HMAC over the canonical payload rather than a bare digest. For a *capped*
operation ("transfer up to X", a balancing transfer), the fingerprint compares the request as the client meant
it, so a retry carrying the original cap matches even though the committed amount differed. **When you are a
client of someone else's processor**, never rely on that check: it is optional in every standard and
undocumented at Adyen and PayPal, so a changed body is a new intent and therefore a new key, and you add your
own pre-send guard comparing the outgoing bytes to the bytes stored with that key.

- **source:** Stripe's idempotent-requests documentation; TigerBeetle `exists_with_different_amount` and the
  balancing-transfer exception (`state_machine.zig:4016-4030`); AWS Powertools `IdempotencyValidationError`;
  Square (*"same key + different body → you get an error indicating that you used the idempotency key
  previously"*).
- **scoping note:** the HMAC clause is deliberately confined to the you-own-the-server branch. On the client
  side an attacker able to craft a preimage against your idempotency table already has write access to it, and
  the clause would buy key management and a rotation story for nothing.

**MC6 · MUST · front: three phases, and the first one COMMITs**

Every external money call is three phases and the first one COMMITs. Write the intent row and `COMMIT` it
(`flush()` inside an open transaction is not persistence), then make the call, then record the outcome. On an
ambiguous failure the intent row stays committed: no `rollback()`, no `DELETE`, no compensating write until
the outcome is known. **The implicit forms count:** no `with session.begin()`, `with engine.begin()`,
`@transaction.atomic`, or equivalent context manager may lexically enclose the external call, because such a
block rolls back on exception with no `rollback` token anywhere in the diff. The outcome record and any
outbound event commit in the same transaction as the state change (outbox row or CDC), never as two
independent operations. Every field written pre-effect is **read by the recovery path**: a startup
`resolve_unresolved_intents()` pass loads each `INFLIGHT` row, uses the persisted identity to query the
counterparty, and converges to exactly one effect. A persisted `client_id` that no code path ever reads back
is the same defect as not persisting it.

- **source:** Brandur, atomic phases; microservices.io transactional outbox; Kleppmann's two dual-write
  failure modes. Production code records a **genuine split among mature projects**: hummingbot commits the
  intent before the POST (`exchange_py_base.py`, `start_tracking_order`) and TigerBeetle states the boundary in
  a comment (*"After this point, the transfer must succeed"*, `state_machine.zig:4194`), while **freqtrade
  writes nothing before `create_order`** (`freqtradebot.py:963`) and cannot recover a lost submit, ship
  freqtrade as the worked negative example.
- **example:** a docstring reading "Reserve a local row first so we always have a record even if the Stripe
  call times out" four lines above `db.rollback()`.

**MC8 · MUST · body: dedupe state is as durable as what it protects**

Deduplication state is persisted in the same transaction as the state it protects. An in-memory `_seen_ids`
set, an LRU cache, or a process-local dict evaporates on restart, which is precisely when the standard
recovery path (a REST backfill, a webhook redelivery, a queue redrive) replays every already-applied event.
Write the dedupe row and the balance/position mutation in one transaction, keyed on the counterparty's own
identifier.

- **source:** production code. nautilus_trader rejects duplicate fills by `TradeId` *before* the state
  transition, and hummingbot does the same independently; both hold the dedupe in the persisted order's own
  event list, not in a process-local set.

**MC9 · MUST · front: watermark coverage precondition**

A cursor, watermark or high-water mark advances only inside the same conditional and the same transaction that
verifiably covered the range it is advancing past. Verify completeness of the response before committing
progress: an error, a provider range rejection, a result count at the provider's documented cap, or a
truncated page is a **hole**, not an empty result. A "nothing to do" branch commits nothing: if the query is
guarded by `if (addresses.size > 0)` then the `saveCursor()` is guarded by the same condition, or the cursor
sprints to the head on a fresh deploy and every address registered afterwards can never see a deposit in a
passed block. Re-read any set the loop filters on (address lists, subscription lists) at the same cadence as
the loop, not once per outer iteration.

- **source:** Alchemy's `eth_getLogs` documented per-tier, per-chain caps; go-ethereum
  `eth/filters/api.go` limits.
- **ownership note:** the general form lives here, not in `fin-onchain`, because the same shape recurs on
  settlement-report ingestion and webhook backfills, which are payments-side. ON1 is the `eth_getLogs`
  instantiation.

**MC10 · MUST · front: arrival order is not occurrence order**

Every handler of a pushed or polled event applies two guards.

**Legality.** Enumerate the legal `(state, event)` pairs and reject everything else with an explicit error,
`_ => Err(InvalidStateTransition)`, not a silent `return`. A terminal state accepts exactly the events by
which the venue **corrects a fact you already booked** (a late fill that crossed a cancel ack, a fill void)
and nothing else. A terminal state is never re-opened by a *status* message. Do **not** write "terminal states
are absorbing": see the falsification note below.

**Version.** The watermark is keyed on the entity id, stored independently of the live object, and **the guard
is the write**: `UPDATE watermarks SET v = :v WHERE id = :id AND v < :v`, proceed only on rowcount 1, in the
same transaction as the effect. `if seen_version(id) >= v: return` followed by a write is a TOCTOU that two
concurrent redeliveries both pass. Never write `if existing is not None and event.ts < existing.updated_at:
return`, when a terminal event has already popped the entity: `existing` is `None`, the guard is skipped,
and a replayed pre-snapshot event re-inserts a phantom row.

**The version must be a total order.** `>=` is correct only when it is. Where the source publishes no version,
derive one from the source's own sequence; where the only available version is a **coarse clock** (Stripe's
`created` is second-granularity, and `refund.created` and `refund.updated` on the same `re_…` routinely share
a second), the watermark is the pair `(created, applied_event_ids)` and an event at the same `created` is
admitted unless its id is already in the persisted set. A bare `>=` on a second-granularity timestamp discards
the `succeeded` event and the refund is pending forever. Wall-clock arrival time is not a version and
last-write-wins is not a policy for money.

- **source:** at-least-once is the only implementable delivery guarantee: Kafka KIP-98 states its own
  consumer-side limit, and Kleppmann & Beresford (*Online Event Processing*) show the residue must be absorbed
  by idempotence at the consumer. **The absorbing-terminal clause is falsified by nautilus_trader's source:**
  its exhaustive `(state, event)` table contains `(Canceled, Filled) => Filled`, annotated in source
  `// Real world possibility`, plus a fifteenth order status `Voided` for `(Filled, FillVoided)`. A cancel ack
  and a fill can cross on the wire; the fill is real money and the cancel was a request, not a fact. What
  genuinely stays absorbing is `(Canceled, Accepted)`, `(Filled, Accepted)`, `(Filled, Canceled)`,
  `(Rejected, *)`: all absent from the table and all hitting the deny-by-default arm.
- **example:** a replayed pre-snapshot `PARTIALLY_FILLED` re-inserts a phantom open order; `reconcile`
  then sees the client id in `live`, skips re-placing, and the bot believes it is quoting with nothing on the
  book.

**MC12 · MUST · front: the lock, the key, the subject, the duration**

A lock is held for the entire check→act critical section; its key is byte-identical in every process that
takes it; and the key it locks is the key the act mutates. Three mechanical checks.

**(1) Duration.** The transaction boundary encloses the act, not just the check.
`with engine.begin() as conn: SELECT ... FOR UPDATE` releases at the dedent, before the sign and broadcast it
was meant to protect. `FOR UPDATE SKIP LOCKED LIMIT 50` followed by `fetchall()` and a closed transaction lets
N workers process the same 50 rows. `async with session_factory()` with no `session.begin()` holds nothing. A
declared-and-never-acquired `asyncio.Lock()` holds nothing.

**(2) Key determinism.** `pg_advisory_xact_lock(hash(chain) & 0x7FFFFFFF)` uses Python's `hash()`, which is
salted per interpreter by `PYTHONHASHSEED` **for `str`, `bytes` and `datetime`**, verified locally:
`hash('ethereum')` returns a different value in every process, while `hash(1) == 1` in all of them. The
observed defect hashes a chain *name*, so it manifests; had `chain` been an integer chain id it would not,
and a reviewer who checks would discount the whole rule. State it precisely and require a stable digest
(`crc32`/`blake2b` of the UTF-8 bytes) or a small integer registry regardless.

**(3) Subject.** The locked key is the key the act mutates. `FOR UPDATE` on the withdrawal row while
broadcasting a transaction keyed on the *nonce* satisfies both other clauses and still races.

- **source:** CPython's documented hash randomization, `PYTHONHASHSEED` salts `hash()` for `str`, `bytes`
  and `datetime` but not for `int`, reproduced locally (`hash('ethereum')` differs in every process, while
  `hash(1) == 1` in all of them). PostgreSQL's transaction-isolation documentation for the duration clause:
  a `SELECT`-then-`UPDATE`-with-computed-value is not protected by re-fetch the way a single conditional
  `UPDATE` is.

**MC13 · MUST · front: no legal value doubles as "unset"**

A money-path function whose input is absent raises or returns an explicit absent type; it never returns `0`,
`""`, `-1`, `null`, `0x00` or the last-known value. **Prices may be negative; quantities may not**. State it
in that direction, because "a price is not non-negative" is a double negative that produces
`assert price >= 0`. Concretely: `unrealized_pnl()` and `notional()` with no mark price ever set must not
return `Decimal(0)`: a risk consumer reads that as "flat, no exposure" on a live open position, and
`except ValueError: unrealized = Decimal(0)` inside a `snapshot()` re-introduces the same lie one layer up.

- **source:** **FCA Final Notice, Citigroup Global Markets Ltd, 17 May 2024, ¶4.27**, the single most
  implementable finding in the regulator record: an unavailable index price **defaulted to `-1`**, PTE computed
  `quantity × -1` and rendered `-58,000,000`, and the trader read the number they expected and clicked Execute.
  The same missing feed blanked the wave-notional soft block (*"Due to lack of market data, Wave notional
  cannot be found"*, ¶4.30) and it proceeded anyway. **The confirmation control was defeated by a sentinel
  default in a price lookup.** Also: Nomad, Aug 2022, trusted root initialised to `0x00`, the same value
  meaning "not proven", and `process()` then accepted every message, 1,175 withdrawals. Robinhood fn 15
  (securities *"incorrectly returned as having zero value for mark-to-market valuations"*). IBKR/CME
  2020-04-20 negative crude settlement for the negative-price half. Confirmed in production code:
  nautilus_trader's `create_inferred_fill` returns `None` and warns rather than substituting 0.
- **companion:** LG4 owns the collateral-damage case. Read them together; see the footgun note there.

**MC14 · MUST · body: a ceiling that warns is not a control**

A ceiling **rejects** the proposed operation, in the same transaction as the write, before any external effect
(declining costs nothing but the operation). At least one ceiling is an **aggregate** over the batch, wave or
basket, not only per item: per-item limits are satisfiable by an unbounded number of items. The kill switch
is exercisable faster than the loss accrues, and **the component that tripped it cannot reset it**. The
anomaly signal goes to a channel named by a **config key with no default that raises at import if unset**,
not to a log line, a metric nobody alerts on, or a distribution list. Where a per-entity override exists on a
solvency, credit-limit or liquidation check, that override is itself an escalator: it raises the tier by one
(`architecture.md` §6) **and** every change to it is field-level audit-logged (who, when, old value, new
value), with no code path able to set it without one.

- **source:** **FCA/CGML ¶4.18(a), ¶4.33**, per-item hard blocks (US$2bn notional, 200m shares) let
  **US$196bn** through, and the notice states: *"had a basket level wave notional hard block limit been in
  place … the trading incident would not have occurred."* **FCA/CGML ¶4.28/¶4.31**, 711 warnings, 18 visible,
  no forced scroll, one "Override soft warnings" button. **SEC Rel. 34-75331 (Goldman Sachs, 30 Jun 2015)
  ¶8/¶9/¶31**, automated blocks lifted repeatedly 8:44–9:32 without the required authorization, by the author
  of the policy being violated. **SEC Rel. 34-70694 (Knight Capital, 16 Oct 2013) ¶23/¶24**, the 33 Account's
  $2m limit was *"linked to no automated controls"*. Citigroup's April 2024 **$81 trillion credited where $280
  was intended** is corroborating but **SECONDARY** (FT, via CNBC/CBS/Bloomberg); "no ceiling of any kind
  existed on the path" is an inference, not a quoted finding. Compound is cited for the kill-switch clause with
  its correct figure: **~168,000 COMP (~$50M) actually claimed**, bounded worst case ~$80–83M, and no admin
  control could disable distribution inside the 7-day governance process.
- **tier gate:** the field-level audit-log clause fires only when the override predicate holds. It is correct
  for a bank and absurd for a 300-line bot, and the predicate is what separates them.

**MC15 · MUST · front: classify the failure signal, and carry the classification**

For every external effect, write down the counterparty's failure signals and classify each as **DEFINITE-NO**,
**DEFINITE-YES** or **UNKNOWN**, and carry that classification to the decision point instead of flattening it
into a generic exception. A single `except Exception:` around a value-moving call destroys the classification
and is the defect. UNKNOWN paths query by the minted identity before any retry. **A path may be classified
DEFINITE-NO only where the counterparty documents, for that exact code, that the request was not enqueued;
absent that document it is UNKNOWN.** DEFINITE-YES paths record the outcome. Where the counterparty's status
code for "key seen, body differs" is undocumented or inconsistent across providers (422 IETF · 409 Stripe ·
400 OASIS · `IdempotentParameterMismatch` AWS), do not branch on the code: branch on **"not a clean 2xx for my
key" ⇒ UNKNOWN ⇒ reconcile**.

- **source:** the per-provider divergence in the "key seen, body differs" status code (422 IETF · 409 Stripe
  · 400 OASIS · `IdempotentParameterMismatch` AWS), which is why the rule branches on "not a clean 2xx" rather
  than on the code. Binance: *"It is important to NOT treat this as a failure operation; the execution status
  is UNKNOWN."* TigerBeetle's `transient()` (`tigerbeetle.zig:318`) sharpens the axis (*can retrying with
  identical request data produce a different outcome?*): insufficient funds is transient, a payload conflict is
  not; CCXT's `OperationFailed`/`ExchangeError` split tries to say this and gets it wrong. **The permissive
  modal was removed from the DEFINITE-NO branch**: it sat on the only branch that can duplicate money, and a
  misclassified 400 pays twice.

**MC16 · MUST · reference: change and rollout surface**

Never repurpose a flag, enum, or field a deployed consumer still reads: **grep every deployed artefact for
readers before reusing the value**, and delete dead money paths rather than leaving them callable. When a
shared helper is relocated or reused by a second caller, **re-execute every existing caller under test before
the change lands**. **Every parallel shard, stripe, partition or region of a money path is exercised by the
pre-deployment test, and coverage is asserted per shard**, not by a representative one. Treat rollback as a
change with its own test.

- **source:** Knight Capital, 1 Aug 2012: a repurposed flag, an undeleted dead path, a deploy that reached
  7 of 8 servers, and an untested rollback, stacked in one event. SEC Rel. 34-70694 ¶14/¶41: *"moving the
  cumulative quantity function inadvertently disabled the cumulative quantity functionality in the Power Peg
  code"*, never retested for nine years; and ¶27, the rollback that spread the fault to all eight servers.
  SEC Rel. 34-75331 (Goldman) ¶23 and remediation fn 4: stripes A-H and L-Z tested, I-K not.
- **scope note:** the unemittable clauses of the draft ("verify build identity on every host", "assert config
  parity across regions continuously") were cut. What remains is three things an agent can actually do in a
  diff: a grep, a test re-run, and a per-shard coverage assertion.

**MC17 · SHOULD · reference: time and business date** *(new)*

Every money-path timestamp is timezone-aware UTC with an explicit type, and the **business date** derives from
a named cutoff in a named timezone, never from `date.today()` or a naive `datetime.now()`. Funding intervals,
settlement dates, accrual periods, statement cutoffs, PAY5's failure window and PAY8's retention bounds all
key on this. Never order events across nodes by wall clock: that is last-write-wins, and the result is
"silent and subtle data loss rather than a dramatic crash".

- **source:** Kleppmann, *Designing Data-Intensive Applications* ch. 8, time-of-day versus monotonic clocks,
  and *"if some piece of software is relying on an accurately synchronized clock, the result is more likely to
  be silent and subtle data loss than a dramatic crash."* Beyond that last sentence, this rule is engineering
  judgement rather than a sourced venue, regulator or incident fact, which is why it ships at `SHOULD` and at
  reference cost rather than at the front. See §6.3.

---

### 3.2 `fin-exchange-integration`: 16 rules (10 front)

| id | strength | placement |
|---|---|---|
| EX1 | MUST | front (rule) + reference (venue table) |
| EX2 | MUST | front |
| EX4 | MUST | front |
| EX5 | MUST | front |
| EX6 | MUST | front |
| EX8 | MUST | body |
| EX9 | MUST | body |
| EX10 | MUST | body |
| EX12 | MUST | front |
| EX13 | MUST | body |
| EX14 | MUST | front |
| EX15 | MUST | front |
| EX17 | MUST | body |
| EX18 | MUST | reference |
| EX19 | MUST | front (required output slot) |
| EX20 | MUST | front |

---

**EX1 · MUST · front (rule) + reference (table): the client order ID**

**Front, two lines:** Treat a client order ID as a **correlation key, never an idempotency key**, unless the
venue documents that resending it returns the original order. Persist the ID, the full request payload and a
send timestamp durably before the socket write; on an ambiguous response **resolve by querying that ID**, and
resubmit only where the venue's documented collision window is still open at the moment the retry would land.
**Query-first is the default; the reference table is the exception list.** Look up your venue string in
`references/venue-clordid.md` before writing any recovery path.

**Reference, the per-venue conditionals** (all eleven rows; the draft carried eight, and a Kraken FIX
integration matched no row):

| Predicate (observable) | Action |
|---|---|
| Docs say a duplicate ID **returns the existing order** (Coinbase Advanced Trade) | Retry the identical create-order. It is both the retry and the query. |
| Uniqueness only **among open/pending orders** (Binance spot & futures, OKX, Kraken REST/WS) | Never retry. Query by client ID → open orders → history → fills, **within the venue's retention bound**. |
| Client ID **strictly increasing**, regressions dropped (Nasdaq OUCH `UserRefNum`) | Resend the identical message. Re-derive the next ID from the venue (`Account Query Request`), not from local state. |
| **`PossDup=Y` at the same `MsgSeqNum`** (FIX ResendRequest) | Session layer dedupes by sequence number. Do not pass it to business logic. |
| Resend under a **new `MsgSeqNum`** (FIX) | Set `PossResend=Y`, same `ClOrdID`. Confirm in writing that the venue implements the application check; **if it does not, or if it disables ResendRequest (Binance FIX), fall back to query-first.** |
| Uniqueness extends **across the FIX session** (Kraken FIX) | Resend under the same `ClOrdID` is safe for the life of that session, and only that session. |
| Documented uniqueness, **no retention window** (Bybit `orderLinkId`) | Query-first. A rejection proves the order exists but not which attempt made it. |
| A **tag, not an identifier** (Deribit `label`) | Never key on it. Recover by label+instrument+time and accept collisions. |
| A **signed nonce** (Hyperliquid) | Resend the byte-identical signed action with the identical nonce. Never re-sign. |
| Query returns **not-found immediately after** an ambiguous submit | Not evidence of non-creation. Re-query with backoff, then fills, before concluding. |
| Query, retried, still cannot resolve | Hold `INFLIGHT_UNKNOWN` at full notional in risk, close the instrument's gate, escalate, under EX2's deadline. Never resubmit. |

- **source:** each venue's own primary documentation, read directly. OKX: *"Once an order reaches a terminal
  state (filled, canceled, mmp_canceled), the same clOrdId may be reused for a new order."* Coinbase Advanced
  Trade: *"If the ID provided is not unique, the order will not be created and the order corresponding with
  that ID will be returned instead."* Nasdaq OUCH 5.0 (`UserRefNum` day-unique and *strictly increasing*; *"all
  Inbound Messages may be repeated benignly"*; `Account Query Request` for `NextUserRefNum`); FIX 4.4
  `PossDupFlag(43)` and `PossResend(97)`; Kraken's client-order-identifier guide (uniqueness *"across open
  orders … per client"*) and its FIX session scope; Bybit `orderLinkId`; Deribit `label` (max 64 chars, a tag);
  Hyperliquid's signed-nonce model. Production code adds the durability corollary: nautilus_trader embeds a
  date and a counter that must be **restored** across restart, and hummingbot's client order IDs are
  process-scoped and not reproducible.
- **placement note:** the predicate is a venue string, perfectly observable, which is the exact condition for
  a reference pointer. Seven of the eleven rows are dead weight in any given session and would crowd out
  EX14/EX15, which fail more often.

**EX2 · MUST · front: the ambiguous response ladder, with a deadline**

HTTP 5XX, `-1006`, `-1007`, a socket timeout and a 429 are all **UNKNOWN**, not failure: do not resubmit;
query by client order ID. `-2013 NO_SUCH_ORDER` immediately after placement is **not** proof of non-creation:
Binance documents three data sources (Matching Engine / Memory / Database) with different staleness and
asynchronous propagation, so re-query with backoff across the propagation window, then open orders, then
history, then fills, stopping at the first rung that returns a definite answer. `-2011 CANCEL_REJECTED` is
expected in normal operation. Cancel/replace is not atomic: `-2021` means one leg succeeded. **Do not use
ccxt's documented timeout-recovery procedure**: it calls `fetchBalance()` and checks whether the balance
changed, which is race-prone against fees, funding, and other strategies on the same account. If the ladder
still cannot resolve it, hold the order `INFLIGHT_UNKNOWN` at full notional in the risk calculation, close the
risk gate for that instrument, and escalate. **That state carries a wall-clock budget**, declared as a config
value: past it the system takes a defined **risk-reducing** action automatically (cancel-by-client-ID, then
flatten the instrument) rather than waiting for a human. Never resubmit.

- **source:** Binance's own error table and general API information: the 10 s matching-engine timeout behind
  `-1007`, `-1006 UNEXPECTED_RESP`, the three documented data sources (Matching Engine / Memory / Database)
  with different staleness, `-2011 CANCEL_REJECTED` as normal operation, and HTTP 409 with `-2021`/`-2022` on
  cancel/replace. CCXT's manual documents the `fetchBalance()`-based timeout-recovery procedure that this rule
  tells you not to use, contradicts itself elsewhere in the same manual, and will re-POST a create-order on
  timeout if one option is enabled; hummingbot marks a submit timeout `FAILED`, which is a guess.
- **deadline rationale:** "hold, close the gate, escalate" with no clock is where the draft stopped. In
  production that leaves the desk dead until a human wakes up, and the honest failure mode is that people
  disable the rule.

**EX4 · MUST · front: the dead-man switch is armed at the venue and called from the invalidation path**

**Arm the venue-native switch at session start**: Binance/Bybit/OKX cancel-on-disconnect, Deribit
`set_heartbeat`, FIX `CancelOnDisconnect`, with a timeout shorter than your reconnect backoff, because a
process that dies cannot cancel anything and the venue's switch is the only one that fires *because you went
away*. **Then** wire the local fallback: an unconditional `cancel_all()` called from inside the
state-invalidation function, the same function that sets `stale`, `disconnected` or `unsynced`, with its
return value checked, for the case where you are still connected but your own state went stale. A
defined-but-uncalled `on_stale`, a `cancel_all` behind a config flag that defaults off, or a comment deferring
the decision to "whoever owns risk" is the defect this rule exists to prevent.

- **source:** venue-native cancel-on-disconnect documentation: Binance USDⓈ-M Futures *Auto-Cancel All Open
  Orders*, Kraken *Cancel All Orders After X*, Bybit *Set Disconnect Cancel All*, Deribit
  `private/enable_cancel_on_disconnect` and `public/set_heartbeat` (*"If your software fails to do so, the API
  server will immediately close the connection"*, and with cancel-on-disconnect enabled, all open orders are
  cancelled), FIX `CancelOnDisconnect`. **Order corrected against production code:** the only implementation
  found across ccxt, freqtrade, hummingbot and nautilus_trader is venue-delegated: a query parameter on the
  order websocket URL (`architect_ax/src/execution.rs:123-125`, default `false`), one adapter out of dozens;
  `grep -rn -i "cancel_on_disconnect\|dead_man\|deadman" hummingbot/ freqtrade/` returns nothing. A
  locally-wired `on_stale → cancel_all()` can only fire while the process is alive *and* still able to reach
  the venue, which is precisely the condition under which the orders were not yet unmonitored. Venue-first,
  local-fallback is the corrected mechanism.

**EX5 · MUST · front: freshness is `now − ts > max_age`, and it gates market data**

Every book, mark, index, quote and funding rate is stored with the **venue's own event timestamp** and a
declared `max_age`, and the order-submission path evaluates `now − ts > max_age` on every tick. Quoting stops
on: age > max_age, a sequence gap, an unsynced book, or an unrenewed `listenKey`, judged from the data, not
from socket state, because a socket can be open and delivering nothing. Resuming quotes after a 60s+ reconnect
backoff against a book with no timestamp is the failure this rule exists to stop.

**Do not confuse this with the ordering guard.** `ts < last_seen` (ordering) and `now − ts > max_age`
(freshness) fail in opposite situations: a perfectly ordered feed that stopped ten seconds ago passes the
first and fails the second, and a system needs both. A reviewer who greps for `stale` will find an ordering
guard and conclude this rule is satisfied.

- **source:** Binance futures documentation on mark vs index vs last price (`PERCENT_PRICE` evaluated
  against **mark price**, `workingType` selecting `MARK_PRICE`/`CONTRACT_PRICE`, `priceProtect`, `-4131`) and
  Deribit's explicit `trigger` enum (`index_price`/`mark_price`/`last_price`); Binance's documented REST/WS
  data-source drift. **Production code is the load-bearing citation:** nautilus_trader's `is_stale`
  (`crates/data/src/aggregation.rs:451`) is `ts_init < self.builder.ts_last`, *an out-of-order guard, not an
  age gate*, and **no project read has a wall-clock max-age gate on market data feeding an order decision**.
  Freqtrade's only `max_age` is a REST cache TTL. The rule is aspirational in production code, which is why it
  must be stated in the `now − ts` form explicitly.

**EX6 · MUST · front: fills fold, they do not accumulate**

Fills are ordered by the venue's own sequence or event time before they touch position state, deduped on
`trade_id` **before** the state transition and rejected rather than ignored, and the dedupe set is written in
the same transaction as the position row. **Average entry price is recomputed as an order-independent fold
over the persisted fill set on every update, never accumulated incrementally**: that is what makes a REST
backfill interleaved with the live socket permanently corrupt the entry price instead of transiently
reordering it. Read cumulative filled quantity from the venue (`executedQty`, `cumQty`), never a delta you
accumulated. Ship the test that proves the fold: ascending and descending fill order produce a byte-identical
average.

- **source:** the same fill arrives on the stream and on the poll. Binance's `executionReport` carries
  cumulative `z`/`Z` alongside last-fill `l`/`L`, and the documentation itself says average price is `Z`
  divided by `z`, accumulating `+= l` is not idempotent under reconnection, replay or a duplicated frame.
  **Production code supplies the default and the test name:** nautilus_trader recomputes `avg_px` by folding
  all surviving fills in `Decimal` (`orders/mod.rs:1355-1382`), with
  `test_avg_px_invariant_to_fill_arrival_order` (`mod.rs:1769`) asserting ascending and descending order
  produce identical results, and `test_avg_px_keeps_a_quotient_no_f64_can_hold` (`mod.rs:1728`). **Freqtrade
  does the same thing independently**: `recalc_trade_from_orders` (`trade_model.py:1265`) walks the orders
  from scratch on every call. Two projects, no shared code, same conclusion.
- **wording note:** the draft offered a menu ("buffer out-of-order arrivals **or** make the fold
  order-independent; pick one and say which in the code"), two equivalent options plus an instruction to
  write a comment. One default (the fold), one escape hatch (a buffer, if the venue's sequence is authoritative
  and you assert it).

**EX8 · MUST · body: commissions in a third asset**

Commission taken in an asset that is neither the quote nor the settlement asset is converted at a recorded
rate and included in `net_realized_pnl`, **or** the returned struct carries an explicit
`fees_unconverted: [assets]` field that every consumer must handle. On a BNB-discount Binance account,
excluding BNB commissions silently overstates headline profit by the entire fee bill. Book the fee in the
currency the venue reports (`commissionAsset`), handle a null or absent fee asset, and subtract a base-asset
commission from the credited quantity before computing what you can sell.

- **source:** Binance's commission FAQ: the fee side flips with the order side (SELL charges on the quote
  notional; *"for orders on the `BUY` side, the received amount would be `quantity`"*, i.e. the fee comes out of
  the base asset you just bought), the BNB discount changes the fee's *currency* and *"does not apply to tax
  commissions or special commissions"*, and `commissionAsset` can be null on non-trade `executionReport`
  events. Confirmed followed in production by all three trading projects: freqtrade `safe_amount_after_fee`,
  nautilus_trader per-currency commission accumulation, hummingbot `cumulative_fee_paid` with rate conversion,
  which is why only the *non-native-asset* residue ships.

**EX9 · MUST · body: gross up before you quantize, and quantize in the safe direction**

A target price is grossed up by the full round-trip commission **before** it is quantized to `tickSize`, and
the quantization rounds **toward the target-preserving side** (up for a sell target, down for a buy target),
never to nearest, which surrenders up to half a tick of the fee markup half the time. Compute the exit from
the actual executed VWAP (`cummulativeQuoteQty / executedQty`), then
`exit = entry_vwap * (1 + target) * (1 + fee_in) * (1 + fee_out)` at the account's effective maker/taker
rates, then quantize. Separately: when the base-asset commission shaves the received quantity, the sell size
shrinks, raise the price to compensate. A nominal +1% take-profit realizes roughly +0.8% at 0.1% round-trip
taker fees.

- **source:** Almgren & Chriss, *Optimal Execution of Portfolio Transactions* (2000), the fixed cost term
  ε is *"the fixed costs of selling, such as half the bid-ask spread plus fees"*; omitting it is not
  conservatism; it is a different strategy. Binance's `PRICE_FILTER`/`LOT_SIZE` definitions for the
  quantization boundary. The direction of the error is always the same: costs understated, profit overstated.
  The quantize-direction clause imports MC3's floor-out/ceil-in doctrine.

**EX10 · MUST · body: positions move without any order of yours**

Funding, ADL, liquidation, settlement and delivery are **required** PnL components, each deduped by the
venue's settlement/income id exactly as fills are deduped by trade id: a bare `balance += funding` with no
settlement id double-counts on every redelivery and backfill. Ingest the venue's income/transaction-history
endpoint, not only the order stream. **Recognise venue-generated orders as yours.** Filtering "orders that are
mine" by "orders whose client ID I generated" excludes exactly the events that change PnL without your
consent.

- **source:** **attribution matters here.** The identifying markers, Binance's `autoclose-`,
  `adl_autoclose`, `settlement_autoclose-` and `delivery_autoclose-` client-ID prefixes, and Bybit's empty
  `orderLinkId` on venue-initiated fills, are documented by **NautilusTrader's Binance and Bybit integration
  documentation**, not by Binance or Bybit. Ship them as "NautilusTrader's Binance/Bybit adapters identify
  these as…", and verify against your venue's current docs before keying on a prefix. The underlying mechanism
  (funding, ADL, liquidation, settlement and delivery all mutate balance and position) is stated by the
  venues' own income/transaction-history endpoints.

**EX12 · MUST · front: reconcile position and realized PnL against the venue**

A scheduled job queries the venue for position and balance and asserts `Σ(signed local fills) == venue
position size` and `local realized PnL == the venue's own realized figure`, **per `(symbol, positionSide)`**.
Where the venue publishes its own realized-PnL figure on the execution event, cross-check on every event, not
only on the schedule. **Choose the cadence to exceed the venue's documented replication lag**: Binance labels
each endpoint's data source (Matching Engine / Memory / Database) and warns that "the API system is
asynchronous, so some delay in the response is normal and expected": the private stream is ME-sourced and
many REST reads are not, so a reconciliation that runs faster than the lag oscillates. On mismatch, record the
venue's value, close the risk gate for that instrument, and reopen only on a successful reconcile.
**Reconciliation tolerance is expressed in the instrument's own tick, not a fixed epsilon.**

- **source:** Binance's documented REST/WS drift and its mechanism: each endpoint is labelled with its data
  source (Matching Engine / Memory / Database) and the docs warn that *"the API system is asynchronous, so some
  delay in the response is normal and expected."* Production code: nautilus_trader's
  `is_within_single_unit_tolerance` (`positions.rs:423`), a **startup** reconciliation gate
  (`live/src/node/mod.rs:440`), and a continuous check whose result is **discarded** (`let _ =` at
  `engine/mod.rs:1737`). The best platform read computes this number and throws it away; that is the worked
  example.
- **cut:** the draft named `GET /fapi/v2/positionRisk`, `GET /fapi/v2/account`, "the income endpoint" and
  `ORDER_TRADE_UPDATE.o.rp`. None could be verified against Binance's published documentation, and the `/v2/`
  paths do not exist. **Never ship an unverified endpoint path or field name.** The mechanism is
  venue-agnostic; the paths are the reader's to look up.

**EX13 · MUST · body: key positions on `(symbol, positionSide)`**

Key positions on `(symbol, positionSide)`, not on symbol alone, and read the account's position mode at
startup. In hedge mode a LONG +5 and a SHORT −3 collapse into a fabricated flat −3 when `positionSide` is
dropped, and every downstream sizing, flatten and liquidation-distance decision is then computed from a
position that does not exist. Check reduce-only preconditions against the mode: reduce-only is unavailable in
Binance hedge mode and on Bybit spot, `closePosition` is incompatible with `quantity`, and oversized
reduce-only orders are split by Bybit or rejected by Binance with `-2022`.

- **source:** Binance USDⓈ-M Futures documentation, `reduceOnly` *"cannot be used in Hedge Mode"*,
  `closePosition` incompatible with `quantity`, `-2022 ReduceOnly Order is rejected` when a conflicting open
  order exists; Bybit, reduce-only unsupported on Spot, oversized reduce-only orders split into multiple
  orders, `positionIdx` 0/1/2 for one-way versus hedge mode.

**EX14 · MUST · front: what the snapshot does not do**

After a reconnect, re-snapshot and then do four things the snapshot does not do. **(1) Gate:** `mark_ready()`
is called after `on_resync` completes, never before, and order submission is blocked until it is: the
ordering bug is the whole failure. **(2) Gap:** compute what was missed by diffing the snapshot against
persisted state and emit a synthetic missed-fill event, because recovering net position leaves realized PnL
permanently short. **(3) Durability:** `last_trade_id`/`last_update_id` is persisted to disk or Redis, not held
in memory, or every cold start `continue`s past the reconciliation entirely. **(4) Pagination:** the backfill
loops until the venue returns fewer rows than the page size; a single unpaginated call silently truncates the
gap.

- **source:** Binance's *Keepalive User Data Stream* documentation, 60-minute `listenKey` lifetime, and
  *"if the account has an active `listenKey`, that `listenKey` will be returned and its validity will be
  extended for 60 minutes"* rather than rotated. Production code: reconciliation-synthesised IDs must be
  **deterministic over venue-supplied fields including a venue timestamp** (nautilus_trader
  `reconciliation/ids.rs:103-107`), so the same inference after restart dedupes against itself.

**EX15 · MUST · front: the user-data redelivery guard**

The redelivery guard for user-data events keys its watermark on the **client order ID**, persists it
independently of the order object, and applies MC10's two guards: an enumerated legal-transition table with a
deny-by-default arm, and a monotonic version guard implemented as the write itself. **Balances get the same
guard, not just orders.** Write `if last_seen[client_id] >= event.update_time: return` only where
`update_time` is a total order; `if existing is not None and event.update_time < existing.update_time: return`
is the bug, because a terminal event has already popped `existing`: the guard is skipped, and a
replayed pre-snapshot `PARTIALLY_FILLED` re-inserts a phantom open order that `reconcile` then sees in `live`
and declines to re-place.

**A late fill on a cancelled order is not a redelivery.** `(Canceled, Filled)` is a legal transition and the
fill is real money; `(Canceled, Accepted)` is not and must error. The draft's "a terminal order never returns
to an open state" would have discarded a real fill that crossed a cancel ack.

- **source:** nautilus_trader's exhaustive `(state, event)` transition table supplies both the enumeration
  and the falsification of "a terminal order never returns to an open state". hummingbot supplies the negative
  example: its order status updates have **no version guard and no terminal protection**
  (`in_flight_order.py:342`, unconditional assignment), and it decides terminality by a float tolerance on
  `Decimal` money.
- **example:** the bot believes it is quoting; the book holds nothing; the hedge leg is naked and invisible.

**EX17 · MUST · body: overfill is exposure, not an exception**

On an overfill or any venue-vs-local quantity disagreement, **record the venue's reported quantity unclamped**,
add the excess to an `overfill_qty` **field** (a field, not a log line), alert, and close the risk gate for
that instrument. The gate blocks `submit_order` and any size-increasing amend **only**: `cancel_all(scope)`,
`flatten(scope)`, position, PnL and margin must all keep working while it is closed, **and there must be a
test that proves it**. Position and PnL are recomputed as a fold over the fill set including `overfill_qty`,
the extra units are real exposure. The gate reopens only on a successful reconciliation against the venue,
never on a timer and never from the code path that closed it. Never `abort`, never clamp, never silently drop
the event.

- **source:** Ariane 501 Inquiry Board report, *"It was the decision to cease the processor operation which
  finally proved fatal"*; SEC Rel. 34-70694 (Knight) ¶42, disconnect the emitter, keep managing the position.
  **Production code is the decisive support:** nautilus_trader's `allow_overfills` defaults to `false`, and
  with `false` the reconciliation path **discards the fill** (`reconciliation/orders.rs:785-796`,
  `return None`) while the live path `bail!`s: the default behaviour is to throw away a fill report the venue
  sent you, and the only trace is a WARN line. The order model is already ready for the right answer:
  `overfill_qty` accumulates at `mod.rs:1261-1268` and the invariant assertion at `mod.rs:1341-1352` is
  deliberately an **inequality**. The capability exists; the config default withholds it. That is the strongest
  possible argument for keeping this rule: the people who thought hardest about it chose the dangerous default
  and made safety opt-in.

**EX18 · MUST · reference: book synchronisation**

Follow **your venue's** exact snapshot/incremental join algorithm and nothing else. Binance Spot and Binance
Futures use different algorithms and the wrong one is the single most-copied incorrect snippet in the
ecosystem. On any sequence gap, **discard the book and re-snapshot, never patch.**

Venue-specific facts below are **dated and volatile; re-verify against the venue's current documentation
before relying on any of them**: OKX's checksum field is always 0 from 2026-06-23 in production, so validate
`seqId`/`prevSeqId` instead (OKX's own deprecation notice); Bybit `u == 1` means a service restart, not an
update; Kraken's CRC32 covers exactly the top 10 levels.

- **source:** each venue's own book-management documentation. Binance Spot *"How to manage a local order
  book correctly"* and the **different** USDⓈ-M Futures algorithm (first event `U <= lastUpdateId AND
  u >= lastUpdateId`, then every subsequent event's `pu` must equal the previous event's `u`); Kraken's WS v2
  book channel and CRC32 checksum guide (top 10 levels regardless of subscribed depth); Bybit's public
  orderbook channel (`u == 1` ⇒ service restart); OKX's checksum-deprecation notice (*"the checksum field will
  remain present in push messages but will always return 0 and must no longer be used"*, production
  2026-06-23, migrate to `seqId`/`prevSeqId`).
- **scope note:** one reviewer would cut this entirely: the volatile facts will be false within a year and
  the suite has no update path, and anyone hand-rolling book sync from an LLM is already in trouble. Half
  accepted: the durable mechanism stays, the volatile facts stay but carry their as-of dates and an explicit
  re-verify instruction, and the whole thing sits at reference cost behind a venue-string predicate.

**EX19 · MUST · front: REQUIRED OUTPUT SLOT: two tests, emitted as code**

Before any bot runs against live keys, emit these two tests **as code in the same response**.

**(a) The timeout that already filled.** Put a Toxiproxy `timeout` toxic in front of `POST /order` (or stub
it) so the request **is delivered upstream** and then returns 503; assert the bot does not resubmit, that it
calls query-by-clientOrderId, and that internal state afterwards shows **exactly one** order, then run the
mirror case where the order genuinely does not exist and assert the retry does occur.

**(b) The filter property test.** Generate `(price, qty)` across the realistic range including values near
`minQty`, near `minNotional`, and with more decimal places than `tickSize`/`stepSize` allow; assert every
filter holds **simultaneously**, that normalization never returns `qty == 0` without an explicit skip signal,
and that rounding is always toward validity; parameterise over LIMIT and MARKET so `MARKET_LOT_SIZE` is
exercised; drive filter values from a **production** `exchangeInfo` fixture.

- **source:** `architecture.md` §9 step 7 (required output slot); Toxiproxy's `timeout` toxic semantics
  (*"stops all data from getting through, and closes the connection after timeout"*); Binance's published
  filter definitions (`PRICE_FILTER`, `LOT_SIZE`, `MARKET_LOT_SIZE`, `NOTIONAL`/`MIN_NOTIONAL`) for the
  property test's fixture and its simultaneity requirement.
- **why a slot and not a pointer:** an omitted element gets added by a **required slot in a template the
  author is already filling**; it does not get added by a prose pointer to another skill plus a verb ("test")
  this user never says. **If the suite is ever compressed, keep EX19.** These are the two tests that cost the
  most when they are missing, and every competing design stranded them behind a cross-skill name reference.

**EX20 · MUST · front: the pre-trade limit check, colocated** *(new)*

`max_order_notional`, `max_position`, `max_orders_per_second` and a **price-deviation band derived from that
instrument's own reference price** are evaluated **inside the function that calls `submit_order`**, before the
send, reading live position, not by a monitor reading a metric, and not in a sibling module. Exposure is
measured from **orders entered, not executions received**. The same band derivation applies on every
session-state code path (pre-market, auction, halt, continuous), and a duplicate-order control is calibrated
per counterparty.

- **source:** **17 C.F.R. § 240.15c3-5(c)(1)**, verbatim: *"(i) Prevent the entry of orders that exceed
  appropriate pre-set credit or capital thresholds … by rejecting orders … (ii) Prevent the entry of erroneous
  orders, by rejecting orders that exceed appropriate price or size parameters, on an order-by-order basis or
  over a short period of time, or that indicate duplicative orders."* Adopting release Rel. 34-63241: *"the
  broker-dealer's controls must be applied on an automated, pre-trade basis, before orders are routed … must
  assess compliance with the applicable threshold on the basis of exposure from **orders entered** … rather
  than relying on a post-execution, after-the-fact determination"*, and the fat-finger example, *"a
  systematic, pre-trade control reasonably designed to reject orders that are not reasonably related to the
  quoted price of the security"*. SEC Rel. 34-75331 (Goldman) ¶25/¶30, a pre-market band whose upper bound was
  1.5× the highest closing price of *any* listed option, so a $1 order in any name passed; SEC Rel. 34-70694
  (Knight) ¶25.

---

### 3.3 `fin-payments`: 13 rules (6 front)

| id | strength | placement |
|---|---|---|
| PAY1 | MUST | front |
| PAY2 | MUST | front |
| PAY3 | MUST | front |
| PAY4 | MUST | front |
| PAY5 | MUST | body |
| PAY6 | MUST | body |
| PAY7 | MUST | front |
| PAY8 | MUST | body |
| PAY9 | MUST | body |
| PAY10 | MUST | body |
| PAY11 | MUST | reference |
| PAY12 | MUST | body (SEAM S1) |
| PAY13 | MUST | front |

---

**PAY1 · MUST · front: the refund ceiling is the captured amount, minus everything in flight**

Compute `refundable = captured_amount − already_refunded − pending_refunds − disputed_amount`, per charge and
per currency, **in your own code**, from the charge's `amount_captured`, never from `paymentIntent.amount`
and never from your own `orders.amount_cents`. **Refuse the refund while any dispute on that charge is open**
and while another refund on it is `pending`. Count `pending` refunds against the ceiling, not only
`succeeded` ones, or in-flight money reserves nothing.

- **source:** Stripe's refunds and disputes documentation: *"You can't issue a refund outside the dispute
  process while the dispute is open"*, and for bank-debit methods (SEPA Direct Debit, Bacs, ACH Direct Debit,
  ACSS, AU BECS, NZ bank account debits), *"there's a risk of double refund. If you proactively issue a refund
  while the customer's bank also initiates a dispute, the customer might receive two credits for the same
  transaction."* Refunds draw on the **available** balance, so a pending refund is money already committed.

**PAY2 · MUST · front: the webhook is a trigger; the API is current state**

On every webhook, call the API for the object the event names (`stripe.Refund.retrieve(id)`,
`PaymentIntent.retrieve(id)`, Adyen's payment-details endpoint), and make **every ledger move from that
response**. The payload's `amount`, `status`, `currency` and `metadata` are a snapshot of the moment the event
was queued; order attribution read from payload `metadata` attributes money to whatever the object looked like
then.

- **source:** the vendors' own words. Adyen: *"The status of a payment can sometimes change after you get the
  result code, so we recommend that you do not use the result code to update your order management system."*
  Stripe: *"Use the API or Workbench Inspector to get the authoritative state"*, and event objects are frozen
  at creation, *"you can't change Event objects after creation… if you update a charge, the original charge
  event remains unchanged"*, rendered at the account's API version *at event time*. Confirmed in production
  code: freqtrade's `manage_open_orders` fetches before deciding (`freqtradebot.py:1613`), and nautilus_trader
  reconciles against the venue's `OrderStatusReport`.

**PAY3 · MUST · front: order webhook effects by the object, not by the event**

Persist a per-object watermark keyed on the object id (`re_…`, `pi_…`, `ch_…`) holding the last applied
`created` **and the set of event ids already applied at that `created`**, and apply MC10's guards: the
watermark update is the write; a legal-transition table governs status changes; and `succeeded`, `failed` and
`canceled` never regress to `pending`. **`created` is second-granularity**, and `refund.created` and
`refund.updated` on the same `re_…` routinely share a second: a bare "drop any event whose `created` is at or
below the watermark" discards the `succeeded` event and the refund is pending forever. Event-id dedupe cannot
substitute for this: the late `refund.created` carries a fresh `event.id` your `processed_events` table has
never seen, and it re-arms the money branch. Never order by the signature timestamp.

- **source:** Stripe documents no webhook ordering guarantee, and `created` is second-granularity. The
  coarse-clock clause is the correction to a bare `>=`: without it, the rule with the widest blast radius in
  the suite would **mandate** a money bug wherever the version is a coarse clock.

**PAY4 · MUST · front: mark processed when applied, dead-letter when unresolvable**

Insert the row into `processed_events` **only after the effect was actually applied, in the same transaction
as the effect**. An event you could not resolve (unknown object, missing order, a dependency not yet created)
goes to a dead-letter queue with an alert and is **NOT** marked processed, so the provider's redelivery
still reaches you. Committing an unresolvable event to the dedupe table stops all redelivery and makes the
miss permanent: the dedupe mechanism then works against recovery instead of for it.

- **source:** TigerBeetle's `id_already_failed` states the boundary: a negative outcome must be as durable
  as a positive one, while the dedupe key must adopt the producer's own event identity rather than a local
  "I saw this" marker. Durably record *what happened*; never durably record *"I saw this"* for something you
  did not apply.

**PAY5 · MUST · body: pending is not paid, and a refund can fail later**

`refund.created` means **pending**, not paid: money is not gone until the refund reports `succeeded`. A refund
can move to `failed` afterwards and the ledger must accept that reversal as a **new balancing entry on a
transaction it already closed**. Stripe: *"the bank returns the refunded amount to us and we add it back to
your Stripe account balance. This process can take up to 30 days from the post date."* The full status set is
`pending`, `succeeded`, `failed`, `canceled`, `requires_action`, and a `requires_action` refund can cycle back
to `requires_action` from `pending` when the customer's bank returns the funds. `failure_reason =
charge_for_pending_refund_disputed` is the dispute-landed-while-refund-pending branch, and Stripe's own
guidance is to accept or challenge the dispute rather than reissue. Send an idempotency key on the refund call
itself, and generate a fresh key after any non-409 4xx.

- **source:** Stripe's refunds documentation, quoted verbatim. The full `failure_reason` set is
  `charge_for_pending_refund_disputed`, `declined`, `expired_or_canceled_card`, `insufficient_funds`,
  `lost_or_stolen_card`, `merchant_request`, `unknown`. The 30-day window is scoped to the return-of-funds
  process and quoted, not asserted as a flat fact about all refunds.

**PAY6 · MUST · body: the capture state machine**

**Cancel, do not refund**, an intent in `requires_capture`. Stripe: *"the charge attached to the
PaymentIntent remains uncaptured and can't be refunded directly. You must cancel the PaymentIntent."* A
refund against uncaptured funds returns money that was never taken. A partial capture destroys the remainder:
the uncaptured balance is **released, not held**, and cannot be captured later. Incremental authorization
takes an absolute new total, not a delta. Read authorization deadlines from the processor's response metadata,
never hardcode them; they are scheme- and payment-method-specific.

- **source:** Stripe's capture and cancellation documentation: cancel-vs-refund and the cancellable-status
  list (`requires_payment_method`, `requires_capture`, `requires_confirmation`, `requires_action`, and
  `processing` for US Bank Account); multicapture, where `final_capture` defaults to `true` and the first
  omission releases the remaining authorization; incremental authorization taking a new **total** rather than a
  delta and not extending the capture deadline. Adyen requires capture before refund.

**PAY7 · MUST · front: three sources, three jobs**

The **API is current state**, the **webhook is a trigger**, the **settlement report is the money**. Close the
books on settlement data with a reversal tail, and reconcile on the **processor's own identifier**
(`pspReference` at Adyen, `balance_transaction`/`id` at Stripe), with your `merchantReference` as a grouping
attribute only, because `merchantReference` is not unique. The settlement line for a refund, a dispute debit,
a dispute reversal and a refund failure can share one amount and be disambiguated only by `description`;
**parse it, do not infer**.

- **source:** Adyen's `pspReference` / `originalReference` model and settlement-detail reporting; Stripe's
  `balance_transaction` and Reporting API; `architecture.md` P10 (join on the counterparty's identifier, not
  yours); Revolut: the only effective control was a partner bank's cash-position report.

**PAY8 · MUST · body: an idempotency key sent to a processor does not survive**

It does not survive: retention expiry (Stripe ≥24 h, Adyen ≥7 d, PayPal capture 6 h, Open Banking 24 h,
Powertools 1 h default), cross-region failover (Adyen: *"not be checked for duplication in other regions"*),
the rate limiter (429), the auth layer (401), most validation errors (400), or a different processor. **500s
ARE cached** and mean indeterminate, and Stripe states there is no client-side algorithm that resolves it.
Bound the same-key retry loop by wall clock to well inside the documented retention, **store that bound as an
asserted constant next to the provider's number**, and past it stop retrying, mark the attempt UNKNOWN, and
resolve by querying your own reference or the settlement report. Pin the key to the
`(provider, endpoint/region, credential)` recorded at mint time. Encode the key to the narrowest length limit
across every provider you target (≤255 Stripe · ≤64 Adyen · ≤64 AWS · ≤40 Open Banking) and validate at
construction.

- **source:** the providers' own retention, scope and length statements. Stripe (keys retained ≥24 h; 500s
  are cached and indeterminate, with no client-side algorithm that resolves it), Adyen (≥7 d; a key will
  *"not be checked for duplication in other regions"*), PayPal (`PayPal-Request-Id` stored 6 h for capture,
  with 200-vs-201 as the replay signal), Square, and the UK Open Banking profile (24 h). Length limits:
  ≤255 Stripe · ≤64 Adyen · ≤64 AWS · ≤40 Open Banking.

**PAY9 · MUST · body: do not assert `dispute.amount == charge.amount`**

FX drift between purchase and dispute, issuer aggregation of several recurring charges into one dispute,
partial disputes, and full disputes of partially refunded charges all break it, **and the assertion crashes
the handler, so the dispute goes unanswered past its evidence window and is lost by default.** Dispute
outcomes are not immutable: `lost` can flip to `won` ("late wins") and `charge.dispute.funds_reinstated` must
be accepted after you have already written the loss off. Multiple disputes per payment are possible. Store the
dispute amount and currency separately from the charge amount and currency.

- **source:** Stripe's *How disputes work*, the disputed amount can differ from the charge (FX drift between
  purchase and dispute, issuer aggregation of recurring charges into one dispute, partial disputes, full
  disputes of partially refunded charges), multiple disputes per payment are possible, and **late wins** flip
  `lost` to `won`; `charge.dispute.funds_reinstated` appears in Stripe's own refund-events table. See also VF2:
  the disputed amount crossed a network, so an assertion on it is an operating error wearing a
  programmer-error costume.

**PAY10 · MUST · body: marketplace refunds reverse the transfer**

On a marketplace refund, reverse the connected-account transfer **in the same unit of work as the refund**: a
refund without the reversal takes the money from the platform. Never create a transfer against a payment whose
method settles asynchronously until it has settled. `transfer_group` is a reporting label, not a functional
link: Stripe states *"it doesn't affect any standard functionality"*; it does not cause a reversal and does
not join anything at settlement. A transfer reversal can itself fail for lack of funds on the connected
account.

- **source:** Stripe's *Separate charges and transfers* documentation: *"The `transfer_group` only identifies
  associated objects. It doesn't affect any standard functionality"*; *"refunding a charge has no impact on any
  associated transfers"*; and *"Stripe debits your platform for refunds to destination charge or separate
  charge and transfer payments. **Reverse the transfers associated with these charge types to recover the
  refund amount from your connected accounts.**"* Reversal succeeds only if the connected account's available
  balance covers it, and Stripe does not automatically reverse a transfer when an async payment later fails.

**PAY11 · MUST · reference: rails, reversibility, and the one load-bearing sentence**

**Verify the destination before a send on any irreversible rail. Wires, RTP and FedNow are irreversible on
send and there is no recall to fall back on.** That sentence is the rule; the taxonomy below is a lookup.

Classify every rail by reversibility and window before writing the send path: card chargebacks ~120 days (180
for some local payment methods, or from the event date for future-dated services); ACH returns by R-code with
per-code windows, **R10** ("Originator not known and/or not authorized to Debit Receiver's Account") is
returnable for **60 calendar days** after settlement, while administrative returns (**R01** NSF, **R02–R04**,
**R09**) are due within **2 banking days** of the settlement date; SEPA distinguishes **Reject** (before
inter-PSP settlement), **Return** (after settlement, by the Beneficiary PSP), **Recall** (by the Originator
PSP) and **RFRO**, with distinct windows and outcomes, and answering a Recall is not the same as returning the
money.

- **source:** Nacha's unauthorized-return rules, **R10** at **60 calendar days** after settlement,
  administrative returns (**R01** NSF, **R02–R04**, **R09**) within **2 banking days** of the settlement date;
  the EPC SCT Rulebook (EPC125-05) and its R-transaction guidance (EPC135-18) for Reject / Return / Recall /
  RFRO; the FedNow Service Operating Procedures for irreversibility, *"settles with finality when the FedNow
  Service records the debit and credit"*, no reversal primitive (a return is a new `pacs.004`), and a
  `camt.056` return request may be answered **`RJCR`: "Return request rejected and funds will not be
  returned."** **Cut from the draft:** "R29 60 calendar days", under Nacha, R29 is a corporate return on a
  2-banking-day clock, so the pairing was inverted. The EPC 15-banking-day Recall answer window comes from a
  guidance abstract rather than the rulebook PDF and is **marked unverified** rather than quoted.

**PAY12 · MUST · body: SEAM S1 (payments ↔ ledger)**

*The counterpart half of this boundary is stated in `fin-ledger`, from that side.*

Every payment state transition emits **exactly one balanced ledger transaction** whose id derives from the
payment's idempotency key. Every clearing account between payment states returns to zero, monitored as a
continuous assertion. **Never derive a balance by scanning payment objects.** Authorizations are reserved
amounts in the payments layer, not ledger entries; only captures, refunds, disputes, fees and settlement
adjustments post.

- **source:** `architecture.md` §5.6 S1.

**PAY13 · MUST · front: the processing fee is not refunded** *(new)*

A refund returns the **principal**. The processor's original processing fee is **not** returned, and a refund
fee may be charged on top. The ledger group for a refund therefore reverses the principal leg and **leaves the
fee expensed**: it is not the mirror image of the charge's ledger group. Reversing the original charge's full
group on refund creates a permanent, silent, per-refund gap that surfaces only in the settlement
reconciliation PAY7 asks for. `refund_application_fee` and `reverse_transfer` on a Connect charge are
**proportional**, and a partial refund reverses proportionally.

- **source:** Stripe, verbatim: *"you can refund all or part of a payment after it succeeds, which might incur
  a fee. **Stripe's processing fees from the original transaction aren't returned.**"* Also: *"Refunds use your
  available Stripe balance (not including pending amounts)"*, so a refund can itself go pending on an
  insufficient balance; and `refund_application_fee` / `reverse_transfer` are **proportional** on a Connect
  charge.

---

### 3.4 `fin-onchain`: 15 rules (8 front)

| id | strength | placement |
|---|---|---|
| ON1 | MUST | front |
| ON2 | MUST | front |
| ON3 | MUST | front |
| ON4 | MUST | front |
| ON5 | MUST | front |
| ON6 | MUST | body |
| ON7 | MUST | front |
| ON9 | MUST | front |
| ON10 | MUST | body |
| ON11 | MUST | body |
| ON12 | MUST | reference |
| ON13 | MUST | body |
| ON14 | MUST | front (SEAM S2) |
| ON15 | MUST | reference |
| ON16 | MUST | body |

---

**ON1 · MUST · front: an `eth_getLogs` call is complete only if you can prove it**

Check the returned count against **your provider's documented cap for your tier and your chain**. Alchemy
publishes a per-chain, per-tier table (free tier 10 blocks on every chain; PAYG **unlimited on major chains**,
1,000 on some, 10,000 on others; response cap **150 MB**), so read the cap from configuration and never
hardcode one number. Treat any error or range rejection as a **hole**, not an empty result; halve the range
and retry on a range error; configure a multi-provider transport (`fallback([http(a), http(b)])` in viem, an
ordered provider list in ethers) so one provider's outage is not a silent gap. **The cursor advances only
after the range is verifiably covered** (MC9). A backfill loop that catches an error and advances loses
deposits permanently, with no error and no log line.

- **source:** **Alchemy's `eth_getLogs` reference**, free tier 10 blocks on every chain; PAYG **unlimited on
  major chains**, 1,000 on some and 10,000 on others; response cap **150 MB**. The cap is tier- *and*
  chain-specific, which is why the rule says read it from configuration rather than quoting one number.
  go-ethereum `eth/filters/api.go` limits (`errBlockHashWithRange`, `maxTopics`, `logQueryLimit`).

**ON2 · MUST · front: `eth_getLogs` never signals a reorg; chain the parent hashes**

`eth_getLogs` never sets `removed: true`, verified in go-ethereum, where `Removed` is set **only** inside the
reorg path feeding `core.RemovedLogsEvent`, which serves subscriptions and `eth_getFilterChanges`;
`FilterAPI.GetLogs` never sets it. A polling indexer therefore receives **no reorg signal at all** and must
detect reorgs by **parent-hash chaining**: store `block_hash` and `parent_hash` for every processed block and
assert `block.parent_hash == stored_hash[n-1]` before processing block n. An indexer that stores no
`block_hash` cannot detect a reorg deeper than its confirmation lag, ever. Subscriptions are not a reliable
substitute: removals are emitted only by the node that itself processed the reorg while you were connected, so
a reconnect, a provider failover, a node restart or a load-balanced pool loses them, and web3.js #1766
documents `removed=true` delivered **twice** for the same log. Use EIP-1898 block-hash-pinned reads
(`{"blockHash": …, "requireCanonical": true}`) where the provider supports them, or an `eth_call` at `latest`
reads state from a different fork than the log came from.

- **source:** go-ethereum source, read directly: `Removed` is set only inside the reorg path feeding
  `core.RemovedLogsEvent`, which serves subscriptions and `eth_getFilterChanges`; `FilterAPI.GetLogs` never
  sets it (`core/blockchain.go`, `eth/filters/api.go`). QuickNode's Streams reorg documentation for parent-hash
  detection (*"a new block's parent hash does not match the hash of the previous block that was streamed"*);
  web3.js #1766 for `removed=true` delivered twice for the same log; EIP-1898 for block-hash-pinned reads.
  **The widespread claim that `eth_getLogs` signals reorgs via `removed: true` is false for a polling
  indexer**: it is in vendor docs, blog posts and LLM output, which is precisely why the rule must carry the
  geth reference.

**ON3 · MUST · front: the log dedupe identity is four-part, and the credit path checks for an unreversed twin**

The log dedupe identity is `(chainId, blockHash, txHash, logIndex)`, with a unique constraint on all four.
`(txHash, logIndex)` has a real unique constraint and is still wrong for two reasons: it makes re-crediting
after a reorg impossible, because the re-included log collides with the orphaned row that
`ON CONFLICT DO NOTHING` refuses to replace; and a transaction re-included in a different block at a different
`logIndex` passes the constraint and double-credits. **The four-part key only avoids the double credit if the
unwind already landed**, so the credit path additionally asserts that **no unreversed credit exists for
`(chainId, txHash, logIndex)`** before crediting a re-included log; otherwise a reorg detected late produces
a fresh row that passes the constraint and credits twice.

- **source:** TRM's published EVM reorg pipeline, semantic **and** positional deduplication, because a
  re-included transaction can occupy a different index in a different block; `architecture.md` seam S2(i). The
  unreversed-twin assertion closes the second double-credit path that a four-part key alone leaves open.

**ON4 · MUST · front: a reorg unwind is a reversing entry, never a delete and never a bare debit**

A reorg unwind writes a **reversing balancing entry keyed on the orphaned log identity**, never a delete,
never an in-place edit, never a bare debit of the user's balance. Three failure shapes to design out: a revert
path that debits blocks of legitimate deposits which `ON CONFLICT … DO NOTHING` then makes impossible to
re-credit; a `CHECK (amount >= 0)` that aborts the revert transaction and wedges the indexer in a permanent
retry loop; and a rollback deeper than the indexer's floor (graph-node's `ETHEREUM_REORG_THRESHOLD` defaults
to **250** blocks, with the comment *"Blocks cannot be reverted below the reorg threshold"*), which is an
**unrecoverable-state halt** requiring a rebuild from a snapshot or genesis, not a rollback.

- **source:** graph-node source, `ETHEREUM_REORG_THRESHOLD` defaults to **250** blocks in
  `graph/src/env/mod.rs`, with the comment *"Blocks cannot be reverted below the reorg threshold"*; the Polygon
  PoS **157-block reorg** at block 39,599,624 on 2023-02-23, reported on Polygon's own community forum;
  `architecture.md` seam S2(iii). The `CHECK`-blocks-the-reversal shape is LG4's, in a different domain; read
  them together.

**ON5 · MUST · front: transaction identity is `(chainId, from, nonce)`, and the confirmer reads every hash**

Transaction identity is `(chainId, from, nonce)` on EVM and the signature over the recent blockhash on Solana
**never the tx hash**. Store every broadcast hash for a nonce as a **set**, and make the confirmer read the
whole set: a fee bump (a ≥10% increase on **both** `maxFeePerGas` and `maxPriorityFeePerGas` at the same
nonce, geth's `PriceBump: 10`; bumping one field returns `replacement transaction underpriced` and leaves the
original stuck) mines under a **new hash**, so a confirmer that writes `replaces_tx_hash` and never reads it
sees the original tx never confirm, marks the withdrawal failed, and **re-credits the user on funds that
already left**. `already known` from the node means success. A nonce gap stalls everything behind it (geth
defaults: `AccountSlots = 16`, `AccountQueue = 64`, `Lifetime = 3h`).

- **source:** go-ethereum `txpool` `DefaultConfig` (`PriceBump = 10`, `AccountSlots = 16`,
  `AccountQueue = 64`, `Lifetime = 3h`) and `core/txpool/errors.go` error strings (`already known`,
  `transaction underpriced`, `replacement transaction underpriced`, `account limit exceeded`); at most one
  transaction per `(from, nonce)` can ever be canonical, which is what makes the nonce and not the hash the
  identity; Solana's retry guide for the signature-over-blockhash case.

**ON6 · MUST · body: a receipt is not proof, and a missing log is not proof either**

`receipt.status == 0x1` is required before any effect is treated as having occurred. A mined-but-reverted
transaction **consumes the nonce, burns the gas and emits no logs**: the absence of a `Transfer` log is not
proof the send failed, and the presence of a receipt is not proof it succeeded. Contract-initiated
native-currency transfers emit no logs at all and are invisible to a log-only indexer; detect them with
`debug_traceBlock`/`trace_block` or by balance delta. Exchanges that index only ERC-20 `Transfer` events plus
top-level native transactions systematically miss contract-originated deposits.

- **source:** the JSON-RPC receipt specification (`status` `0x1` / `0x0`) and EVM semantics: a reverted
  transaction still occupies a block, consumes the nonce, burns the gas and emits no logs, and a
  contract-initiated native-currency transfer emits no `Transfer` event at all; there is no such event for
  native ETH, so traces or per-block balance diffs are the only detection.

**ON7 · MUST · front: credit the delta you measured, not the number in the event**

Call `balanceOf(address)` before and after (or read the token's own accounting entrypoint) and credit
`after − before`. Fee-on-transfer and rebasing tokens make `Transfer.value` differ from what arrived, and a
token address that is env-overridable (`USDC_ADDRESS` from config) makes this reachable even when today's
asset is not fee-on-transfer. Read `decimals()` **from the contract at runtime** and cache it per
`(chainId, address)`: never a hardcoded 18, 6, or a constant table. Handle non-reverting `false` returns,
calls to codeless addresses that succeed, and the approve-race by setting the allowance to 0 first or using
`permit`, and never resolve an approve-race by granting `type(uint256).max`.

- **source:** the `weird-erc20` catalogue, fee-on-transfer (STA, PAXG), rebasing, non-reverting `false`,
  missing return values, `type(uint256).max`-means-balance, and the ERC-20 specification, in which `decimals`
  is optional and returns `uint8`: GUSD is 2, USDC/USDT 6, WBTC 8, DAI 18, YAM-V2 24. `architecture.md` seam
  S2(iv), credit from an observed value delta, never from an emitted event.

**ON9 · MUST · front: the memo / destination-tag column**

The withdrawal schema carries a `memo` / `destination_tag` column, the API accepts it, and the send path
validates it **per chain** before broadcast. Chains whose exchange deposits are addressed by a shared address
plus a tag silently deliver un-creditable funds when the tag is absent. **Gate the supported-chain set on the
field's existence:** a `SUPPORTED = {(USDT, ethereum), (USDT, tron)}` table that grows to include a
tag-addressed chain without the column is the failure path.

Two chains verified this session, with their types:
**XRP Ledger**: `DestinationTag`, a **32-bit unsigned integer**. XRPL docs: *"a payment to an exchange or
stablecoin issuer address can use a destination tag to indicate which customer to credit"*, and *"receiving a
payment without a destination tag can be a problem: it is not immediately obvious which customer to credit,
which can require a manual intervention and a discussion with the sender."* The receiving account can set
`RequireDest` to make the ledger reject untagged payments.
**Stellar**: `memo`, typed: `MEMO_TEXT` (ASCII/UTF-8, **≤28 bytes**), `MEMO_ID` (**64-bit unsigned
integer**), `MEMO_HASH` (**32-byte hash**), `MEMO_RETURN` (32-byte hash of the transaction being refunded).
Note that Stellar's own docs now point at **muxed accounts** for pooled-account differentiation, so a
Stellar integration must handle both.

- **source:** **XRPL and Stellar primary documentation** for the chain facts quoted above. The draft's
  Cosmos, TON and EOS claims are **cut**: they could not be verified against those chains' own documentation,
  and a front rule may not carry an unsourced factual payload.

**ON10 · MUST · body: a deposit from your own address is not a deposit**

Before crediting a deposit, assert `from_address NOT IN (our deposit addresses ∪ our hot/cold wallet
addresses)`. Moving tokens between two addresses you own emits a **real** `Transfer` to a **real** deposit
address and, unchecked, mints a customer credit with no matching debit. The `from_address` column usually
already exists and is never read; make the credit path read it.

- **source:** the internal-transfer posting rule. A sweep from a deposit forwarder to the base address, or a
  hot→cold move, changes only *which* on-chain account holds the asset: it is value-neutral on the books and
  **customer liability accounts must not appear in the entry at all**. FTX is the named counter-example: a
  customer credit created by an operator action rather than by a settlement event.

**ON11 · MUST · body: confirmation depth comes from a stated reorg-loss budget**

Derive the confirmation depth from a **stated reorg-loss budget**, per chain and per amount, and record the
depth and the budget alongside the credit. "12 confirmations" is folklore: Polygon has produced a 157-block
reorg and forks over 10 blocks deep are routine. **Do not credit an L2 deposit on L2 block count**: wait for
the L1 batch to finalize. If low latency is required, credit immediately **up to a bounded global exposure you
are willing to lose**, rather than lowering the depth globally. Alarm and degrade explicitly if the
`finalized` head stops advancing: Ethereum went non-finalizing for over an hour on 2023-05-12.

- **source:** per-chain observed reorg depths, Polygon's 157-block reorg, Ethereum's 7-block beacon-chain
  reorg of 2022-05-25 (epoch 121471, slots 74→82), and the non-finalizing period of 2023-05-12; Circle CCTP's
  published credit policy, standard transfers on Ethereum/Arbitrum/Base/OP/Unichain/World Chain/X Layer wait
  **~65 Ethereum blocks, 15–19 min**, i.e. an L2 transfer waits on **L1** finality, not L2 blocks, and fast
  transfers are *"subject to a global allowance to mitigate reorganization risks"*, which is the
  bounded-exposure clause in production.

**ON12 · MUST · reference: Solana**

Rebroadcast the **identical signed bytes**; re-sign only after `getBlockHeight("confirmed") >
lastValidBlockHeight`, *"Before re-signing any transaction, it is very important to ensure that the initial
transaction's blockhash has expired"*, or both versions can land and the user *"unintentionally sent the same
transaction twice"*. Query `getSignatureStatuses` with `searchTransactionHistory: true` before concluding a
transaction did not execute; without it only the recent status cache is searched. A non-null `err` means
**landed-and-failed**, not retryable: the fee was paid and the slot consumed. Durable nonces are asymmetric:
a **validation** failure drops the whole transaction with no fee and no state change, while an **execution**
failure after validation still advances the nonce and collects fees; `AdvanceNonceAccount` must sit at
instruction index 0, and the docs warn durable nonces *"may be deprecated in a future release"*. Track blocks
by **hash, not slot number**. RPC nodes drop from the rebroadcast queue above 10,000 outstanding transactions,
and *"if an RPC node can't determine when your transaction expires, it will only forward your transaction one
time."*

- **source:** Solana's own documentation on transaction confirmation and retry, durable nonces, and
  `getSignatureStatuses` (every quotation above is from those pages); `architecture.md` P4 (Solana tracked
  blocks by slot and could not tell two different blocks apart).

**ON13 · MUST · body: the nonce allocator is a single writer, and the lock proves it**

The nonce allocator is a single writer per `(chainId, from)`, and the lock it holds spans
**allocate → sign → broadcast → record**, in one transaction, under a key that is byte-identical in every
replica. `pg_advisory_xact_lock(hash(chain) & 0x7FFFFFFF)` fails **both** halves: Python's `hash()` is salted
per interpreter for `str` (verified: `hash('ethereum')` differs per process, while `hash(1) == 1` everywhere)
so each replica takes a different lock, and `with engine.begin(): SELECT … FOR UPDATE` releases at the dedent
before the sign and broadcast it exists to protect. Use a stable digest of the UTF-8 key bytes and hold the
transaction across the broadcast.

- **source:** CPython's documented `PYTHONHASHSEED` hash randomization for `str`, `bytes` and `datetime`
  (reproduced locally: `hash('ethereum')` differs per process while `hash(1) == 1` everywhere), a salted hash
  is not a lock key across processes. go-ethereum's txpool makes the consequence concrete: two processes
  reading `eth_getTransactionCount("pending")` concurrently allocate the same nonce, and one transaction
  silently replaces the other.
- **relationship to MC12:** ON13 is MC12's withdrawal-nonce instantiation with the domain mechanism
  (allocate→sign→broadcast→record), not a restatement. Its core half is generated from the same source as
  MC12's; see §6.5.

**ON14 · MUST · front: SEAM S2 (onchain ↔ ledger)**

*The counterpart half of this boundary is stated in `fin-ledger`, from that side.*

**(i) Identity**: a deposit credit is exactly one balanced ledger transaction whose idempotency key is
`(chainId, blockHash, txHash, logIndex)`, never the tx hash and never `balance += amount`; the same log
re-observed after a reconnect, a backfill overlap, or a provider failover is a no-op.
**(ii) Staging**: the credit posts on observation to a per-user **PENDING (unavailable)** account and moves
to **AVAILABLE** only at the credit policy's finality (L1 finality for rollups, not L2 block count; below the
policy depth, credit only inside a stated exposure cap you are willing to lose). Withdrawal and onward
transfer authorise from AVAILABLE alone.
**(iii) Unwind**: a reorg detected by parent-hash mismatch produces a reversing balancing entry keyed on the
orphaned log identity, never an in-place edit or a delete; a reorg deeper than the indexer's rollback floor is
an unrecoverable-state halt.
**(iv) Assertion**: a continuous reconciliation asserts `Σ credited at-or-below finalized height ==
Σ observed on-chain value deltas to deposit addresses`.

- **source:** `architecture.md` §5.6 S2; FTX credited ~$8bn of fiat deposits that never left a third party's
  bank account (`architecture.md` P6).

**ON15 · MUST · reference: oracles**

Every oracle read checks `updatedAt` against **the feed's own published heartbeat** (not a number you chose),
clamps against `minAnswer`/`maxAnswer`, reads the L2 **sequencer-uptime feed with a grace period** before
trusting any L2 price, and uses the **FEED's** `decimals()` rather than the token's. `answeredInRound` is
deprecated in the current Chainlink API reference even though audit convention still asks for it. An AMM spot
price is a quantity you can buy, not a valuation. **A single-venue feed is a design defect regardless of
whether anyone attacks it.**

- **source:** Chainlink's current API reference: check `updatedAt` against the feed's own published
  heartbeat, use the **feed's** `decimals()`, and note that `answeredInRound` is deprecated, plus the L2
  sequencer-uptime feed and its grace period. **Citation scoped:** the single-venue clause is carried by
  **Compound's DAI feed, 26 Nov 2020**: the price came from **Coinbase Pro alone**, DAI briefly printed
  ~$1.30 there while Kraken and Huobi stayed near $1.00, and **~$89M of positions were liquidated across 124
  of 225,793 users**. The "adverse market conditions vs manipulation" debate is unresolved and irrelevant: the
  design is wrong either way. Hyperliquid JELLY (2025-03-26) is a perp-DEX mark-manipulation event; it
  supports the single-venue clause and **none** of the `updatedAt`/`minAnswer`/sequencer-uptime mechanics the
  rule spends its words on, and the draft cited it under all of them.

**ON16 · MUST · body: the withdrawal queue's own health preconditions** *(new)*

Before broadcast, the queue asserts its own preconditions and **stops and pages** on breach rather than
broadcasting into a wall. **(1) Nonce continuity:** if `pendingNonce − latestNonce > 0` for longer than a
configured interval, **replace the lowest unmined nonce rather than submitting more transactions**, queued
transactions behind a gap are capped at geth's `AccountQueue = 64` and evicted after `Lifetime = 3h`, so one
stuck low-fee transaction at nonce N blocks every withdrawal behind it and then silently drops them.
**(2) Fee-paying balance:** the broadcasting account's native-gas balance covers a configured multiple of the
current worst-case fee for the queued depth; a hot wallet out of gas stops all withdrawals with no error
anyone reads. Both thresholds are configuration with no default, per ON1's and MC14's pattern.

- **source:** go-ethereum `legacypool` `DefaultConfig`, `AccountSlots = 16`, `AccountQueue = 64`,
  `Lifetime = 3h`. That is the entire nonce-continuity mechanism: one underpriced transaction at nonce N
  freezes N+1…N+k until it is mined or replaced, and the queue behind it is capped and then evicted. The
  gas-balance clause is stated as a precondition **without a fabricated threshold**: the multiple is the
  operator's, because no primary source fixes one.

---

### 3.5 `fin-ledger`: 11 rules (5 front)

| id | strength | placement |
|---|---|---|
| LG1 | MUST | front |
| LG2 | MUST | front |
| LG3 | MUST | front |
| LG4 | MUST | front |
| LG5 | MUST | body |
| LG7 | MUST | body |
| LG8 | MUST | reference |
| LG9 | MUST | body |
| LG10 | MUST | body |
| LG11 | MUST | body (SEAM S3) |
| LG12 | MUST | front |

---

**LG1 · MUST · front: enumerate the legs, then assert the sum, per currency**

Every journal entry group sums to zero **per currency**, including at the external boundary. Enumerate the
legs before writing the insert: the user leg, the counterparty leg (hot wallet, bank, processor), the revenue
leg for a fee you charged, and the expense leg for a cost you actually paid. A withdrawal of `amount` with a
`fee` charged to the user and `gas_spent` leaving the hot wallet is **at minimum four legs**. "The journal
exists" is not the property; "the journal balances" is.

The shipped artifact is `assert all(sum(l.amount for l in legs if l.currency == c) == 0 for c in
currencies(legs))`, **"per currency" goes inside the assert.** A copyable `assert Σ group == 0` passes on
`+100 JPY, −100 USD`.

- **source:** Formance's formal double-entry model (at least one debit and one credit, at least two
  accounts, validated before commit); Uber's zero-sum payments platform, which validates *"the sum of all the
  entries is zero"* **before** the write; Square's Books, *"All transactions (which we call 'journal entries')
  must balance to 0"*; Stripe's Ledger for modelling external counterparties as explicit accounts (`world`,
  nostro, network) so value entering or leaving the system still has two legs.

**LG2 · MUST · front: make conservation structurally unfalsifiable, do not check it at runtime**

The posting API accepts a **SET** of entries and commits them only when they net to zero per currency, so no
caller can create an unbalanced state. **Do not write a runtime "do the books balance" check that halts**: if
it can fire, either a bypass path exists (fix the bypass) or it is not a conservation breach at all but a
**reconciliation** breach against an external record, which is LG3's job and takes a suspense posting, not a
halt.

- **source:** TigerBeetle `exceeds_credits`, *"The transfer was not created."* This is the rule that removes
  the equivocation at the heart of the halt-vs-continue argument: "conservation" was doing double duty as a
  **law over the system's own output** (enforceable, therefore make it structural) and as **agreement between
  two records** (not enforceable, therefore reconcile). LG2 removes the first case by construction; LG3 handles
  the second; MS3 handles the one place where the first case can still fire.

**LG3 · MUST · front: ship the reconciliation, and make it survivable on day one**

Ship it as a **scheduled job**, not as SQL in a comment. It reads through a path **independent of the writer**
(otherwise it finds arithmetic bugs and never a missing write). Discrepancies post to a real
`suspense`/`clearing` account **in the chart of accounts**, not a nullable column and not a log line, so the
trial balance still balances, plus a `break` record carrying `detected_at`, `source_a`, `source_b`, `amount`,
`currency`, `status`. There is an aging policy with a hard escalation threshold and a periodic sweep.
**Opening balances are backfilled before the first run**, or the per-account comparison is broken from day one
and the alert gets muted. Reconcile on three axes, not one: completeness (are all records present?), clearing
(did each clearing account return to zero?), and balance.

- **source:** Stripe's Ledger: clearing accounts carry an expected steady-state balance of zero
  (*"find the clearing Accounts with nonzero balance"*), and reconciliation runs on completeness, clearing and
  balance rather than balance alone; **Federal Reserve Financial Accounting Manual §4.50, Difference account**,
  *"an out-of-balance condition resulting from the normal operation of a department"*, swept monthly;
  **17 C.F.R. § 240.17a-11(c)**: notify same day, remediate in 48 hours, **do not cease operating**. For the
  failure shape: nautilus_trader computes the continuous check and discards it with `let _ =`.
- **companion:** VF11 requires you to prove this job actually detects.

**LG4 · MUST · front: your safety constraints must not block the safety operation**

`CHECK (balance_cents >= 0)` applies to ordinary debits and **not** to compensating entries. **State the
artifact, not the implication:** overdraft is permitted on the **reversal posting type specifically**, as a
partial index or a `CHECK` conditioned on `posting_type`, so that a clawback of already-spent funds can post
while an ordinary debit still cannot. **Do not delete the constraint**; MC13 and LG4 read together otherwise
produce an agent that drops `CHECK (balance >= 0)` outright, which is worse than either failure alone. Make
sure the raw `CheckViolation` cannot escape the typed error hierarchy mid-clawback. An account status check
that raises `AccountNotActive` on a frozen account **blocks the standard fraud flow** (freeze the recipient,
then claw back), forcing an unfreeze that reopens exactly the drain window the freeze existed to close: allow
reversals and clawbacks against frozen accounts, and block only customer-initiated debits. Reversal links are
bidirectional (`reverses_transaction_id`, `reversed_by_transaction_id`) with a **uniqueness constraint on
`reverses_transaction_id`**, so one transaction cannot be reversed twice by two operators or by an operator
plus a retry.

- **source:** Modern Treasury's ledger-transaction model, reversal links stored in both directions with
  uniqueness enforced on "reverses this transaction", so one transaction cannot be reversed twice;
  TigerBeetle's correcting-transfers recipe. The account-status clause is an operability requirement: a control
  that cannot be operated against a hostile counterparty is not a control, and freeze-then-claw-back is the
  standard fraud flow.

**LG5 · MUST · body: materialised balances drift silently**

The materialised balance `UPDATE` is in the **same transaction** as the entry `INSERT`, carries a monotonic
version, and is verified by a separate **order-independent checksum recompute**
(`SELECT SUM(amount) FROM entries WHERE account_id = ?`) that alerts on drift. Never
`INSERT INTO entries …; COMMIT;` followed by a separate `UPDATE balances`. The recompute runs on a schedule
and **does not fix the balance in place**: it raises a break per LG3.

- **source:** Square's Books writes the balance atomically with the journal entry; Modern Treasury and
  Uber's money-order data models for materialised-balance drift, monotonic versioning, and a separate recompute
  job that alerts rather than repairs in place.

**LG7 · MUST · body: a hold expires by itself, and only `available` authorises**

A hold carries an **intrinsic `expires_at` that the READER enforces** (`WHERE expires_at > now()`), not a
release that depends on a callback, a cron, or the happy path completing. Check the invariant **at reserve
time**, so no committed reservation can later be un-postable. Inbound pending is never available:
`available = posted − active_holds`, and only `available` authorises a spend or an onward transfer. Name each
balance you expose (`posted`, `pending`, `available`) and never let one number mean all three.

- **source:** Modern Treasury on naming `posted`, `pending` and `available` and never letting one field serve
  all three, and on computing available as posted minus active holds without adding pending credits; Stripe's
  authorization-window documentation (Visa CNP 7 d, MIT ~4 d 18 h, Mastercard 7 d CNP / 2 d CP, Klarna 28 d,
  PayPal 10+10 d) for intrinsic expiry; TigerBeetle's two-phase pending transfers, whose reservation is
  *always released in full* and whose invariant is checked at reserve time rather than at post time.

**LG8 · MUST · reference: bitemporality**

Separate `effective_at` (when it happened economically) from `created_at`/`posted_at` (when the system learned
it), and make a historical balance query stable under later writes:
`WHERE effective_at <= T AND (discarded_at IS NULL OR discarded_at >= T)`. A back-dated entry must not
silently change a balance someone already reported; a discard must not vanish from history. Entries get
`discarded_at`, never `DELETE`.

- **source:** Modern Treasury's as-of reconstruction (`effective_at` versus system time; `discarded_at`
  rather than `DELETE`); Fowler's *Accounting Patterns*, `whenOccurred` / `whenNoticed`.

**LG9 · MUST · body: currency is a dimension, not a database**

**The property is: every journal group balances per currency, and no expression multiplies or adds two amounts
of different currencies.** An FX movement is a **balanced two-account transaction** with the rate, its
provenance, its side and its pivot recorded on the transaction, and the spread booked to its own revenue
account, never a rate multiplied in place on a balance. Rounding residue from the conversion posts to a named
residue account. The ledger dimension carries currency **and scale** and is immutable: never change the asset
scale or currency of an existing ledger, migrate to a new one.

- **source:** Formance's currency-dimension model and Modern Treasury's per-currency balance condition;
  TigerBeetle's `ledger` field, which partitions by asset and whose asset scale **cannot be changed after
  account creation without migrating to a new ledger**; Selinger's multiple-currency accounting tutorial for
  FX as a balanced two-account transaction rather than a rate multiplied in place.
- **wording note:** the draft said *"one ledger per currency and no cross-currency arithmetic anywhere"*. That
  wording produces separate tables or separate databases per currency, a schema people spend a year
  unwinding. Every ledger cited above (TigerBeetle, Formance, Modern Treasury, Square) is one journal with
  a currency dimension and a per-currency balance constraint on the group. State the property, not an
  implementation that happens to satisfy it.

**LG10 · MUST · body: the solvency invariant and its chokepoint**

State the system-level solvency invariant explicitly, `Σ customer balances <= custodied assets`, **per
asset**, and assert it continuously against the custodian's own figure. Enumerate every function that can
change a balance and show each terminates in the one chokepoint that checks it. **Make the bypass
unrepresentable rather than provable:** `REVOKE UPDATE, DELETE ON balances` from the application role so the
only writer is the chokepoint's own role or a `SECURITY DEFINER` function, with a test asserting the grant is
absent. "Prove each path terminates in the chokepoint" is an architecture-review question an agent will answer
with a paragraph; a revoked grant is a fact a test can read. Any per-account override on a solvency,
credit-limit or liquidation check is an unbounded liability generator: it raises the tier (MC14) and requires
field-level audit logging of every change.

- **source:** **Euler: `donateToReserves` was the single path without the health check, ~$197M.** FTX,
  customer balances versus custodied assets. `architecture.md` P7; Stripe's Ledger on running the trial balance
  as a continuous monitor rather than a month-end ritual.

**LG11 · MUST · body: SEAM S3 (exchange ↔ ledger)**

*The counterpart half of this boundary is stated in `fin-exchange-integration`, from that side.*

Fills are the economically-final fact: **realized PnL, fees and funding post as journal entries; positions do
not.** The ledger transaction id derives from the venue's `trade_id`, and the same fill arriving on both the
stream and the poll must post **once**. A fill reported as final can be busted inside the clearly-erroneous
window, so booked economic history must accept **retroactive reversal as a new balancing entry**, the
position is revisable, the entry is not editable.

- **source:** `architecture.md` §5.6 S3. The bust clause is the ledger-side mirror of MC10's corrected
  terminal-state rule: `(Filled, FillVoided) => Voided` in nautilus_trader's own state machine is exactly the
  venue correcting a fact you already booked.

**LG12 · MUST · front: append-only is a grant, not a comment** *(new)*

The migration **revokes `UPDATE` and `DELETE` on the entries table** from the application role (or installs a
trigger that raises), and a test asserts the grant is absent. A posted entry is never `UPDATE`d or `DELETE`d;
corrections are new records. State explicitly which lifecycle states are mutable: "immutable" in practice
means **immutable once posted**, not immutable from creation, and a naive "everything is immutable" reading
gets that wrong.

- **source:** Modern Treasury, *"a ledger transaction is mutable while pending and immutable once posted"*,
  and its guidance to enforce that boundary in the write path (status-guarded update, check constraint or
  trigger) rather than by convention; Square's Books, *"there are no update statements for the tables
  presented on the diagram, only inserts"*; TigerBeetle; Uber money orders, *"an order once written can't be
  changed in any shape or form"*; Fowler's `ImmutableEntryException`.

---

### 3.6 `fin-matching-and-settlement`: 9 rules (6 front)

| id | strength | placement |
|---|---|---|
| MS1 | MUST | front |
| MS2 | MUST | front |
| MS3 | MUST | front |
| MS4 | MUST | front |
| MS5 | MUST | reference |
| MS6 | MUST | body |
| MS8 | MUST | reference |
| MS9 | MUST | front (SEAM S4) |
| MS10 | MUST | front |

---

**MS1 · MUST · front: journal the input, commit with the mutation, check the publish**

The book mutation and the durable record of the resulting execution **commit together**, and the outbound
publish reads from that durable record. **Journal the INPUT command before matching**, so replay reproduces
the same executions; never treat the in-memory emit as the record. **Check the publish result:**
`let _ = tx.send(ev);` discards a closed-channel or full-queue error and silently drops a published execution,
which no downstream consumer can detect. A crash between the state change and the emit must replay to the
identical event, byte for byte.

- **source:** `architecture.md` §2.3 (journal inputs, not outputs); LMAX, *"the current state of the
  Business Logic Processor is entirely derivable by processing the input events"*, the journaler stores all
  input events durably, and a production bug is diagnosed by copying the event sequence to a development
  machine and replaying it there.

**MS2 · MUST · front: sequence the matching side and persist the command stream**

Sequence the **matching** side, not only the cancel side, and **persist the inbound command stream**,
"deterministic and replayable" is a property you must be able to demonstrate **by replaying**, not a claim in
a comment. Every emitted event carries a gap-free sequence number, consumed on rejects as well as accepts, and
the sequence generator lives **inside the deterministic core** alongside the ExecID / match-number assignment
so replay reproduces both identically. Keep the decision core free of wall-clock reads, RNG, I/O and
map-iteration-order dependence; inject time and randomness as explicit parameters. A cancel that races an
execution must not renumber or un-emit a published execution; corrections travel as a Trade Cancel referencing
the original match number. **An acknowledged cancel must be honoured, or the acknowledgement must be retracted
to the counterparty before the order is executed.**

- **source:** Nasdaq OUCH 5.0 and TotalView-ITCH 5.0, the ExecID / match-number identity model, and the
  requirement that a correction travels as a Broken Trade referencing the match number rather than as a
  renumbering; **SEC Rel. 34-69655 (NASDAQ, 29 May 2013) ¶24 fn 4**, cancels *"acknowledged"* *"immediately
  upon submission"* were nonetheless filled, and notifying members *"was not discussed"*.

**MS3 · MUST · front: checked arithmetic on anything you publish**

Aggregates you publish use **checked arithmetic on the emit path**, not `debug_assert`.
`level.total_qty -= qty` on a `u64` guarded only by a debug assertion wraps to ~1.8e19 in a **release** build
and is published as depth to consumers who trade against it. Use `checked_sub().ok_or(...)` (or the language's
equivalent) and treat an underflow as a **Class E fan-out conservation breach**: halt that transformation at
the smallest scope, do not publish, and do not clamp to zero. **If you saturate rather than check, you must
emit the saturation**: a saturated aggregate is a lie with no exception attached.

- **source:** TigerBeetle overflow-checks every accumulator before mutation (`sum_overflows`), while the
  trading systems use `saturating_add` / `saturating_sub` (nautilus_trader `orders/mod.rs:1270`, `:1366`), the
  *correct* choice where a panic would leave exposure unmanaged, but it means an overflow is silently absorbed
  unless you emit it. Note also that nautilus_trader compiles its `debug_assert!`s out of both dev and release
  (`Cargo.toml` `debug-assertions = false` in `[profile.dev]` **and** `[profile.release]`), which is exactly
  how a debug-only check becomes no check.

**MS4 · MUST · front: a bounded transformation carries a counter and a hard bound, on the emit path**

Every bounded transformation carries a counter keyed to its inbound unit (parent order id, instruction id,
batch id) and a hard bound (`max_children_per_parent`, `max_notional_per_parent`, `max_shares_vs_ADV`,
`max_messages_out_per_message_in`): **checked ON THE EMIT PATH BEFORE THE SEND**, not by a monitor reading a
metric. On breach: set a flag the emit path reads before every subsequent send, cancel resting orders, and
disconnect the order-entry session, while risk, position and drop-copy stay alive. **The flag must not be
resettable by the component that tripped it.** Never disable the failing check as the mitigation. A
suspense/error account is single-purpose and linked to an automated firm-aggregate limit that **rejects new
orders** on breach.

- **source:** **SEC Rel. 34-70694 (Knight) ¶21**, no *"control to compare orders leaving SMARS with those
  that entered it"*, and *"no procedures in place to halt SMARS's operations in response to its own aberrant
  activity"*; ¶17 ($460M in 45 minutes); ¶27 (*"continued to send millions of child orders while its personnel
  attempted to identify the source"*, and the remediation re-armed the defect on seven more servers, *"This
  action worsened the problem"*); ¶23/¶24 (the 33 Account's $2m limit *"linked to no automated controls"*);
  ¶16 (the fill state existed and *"was not communicated to SMARS"*). 17 C.F.R. § 240.15c3-5(c)(1)(ii).
  Samsung Securities 2018, 2.81bn ghost shares.
- **correction:** the draft claimed *"NASDAQ 2012 removed the validation code from the failover path, that is
  what created the >3M-share error position."* Two steps compressed into one. ¶28: *"Because more sell shares
  than buy shares were cancelled during this period, NASDAQ had a more than 3 million share short position."*
  The removal let a 19-minute-stale cross print; the position came from the **cancel imbalance inside that
  window**. NASDAQ belongs under MS5 and under prohibition 3, not here.

**MS5 · MUST · reference: revalidate-and-recompute loops**

Never compute an auction or cross price over a book that concurrent cancels can mutate between compute and
print. **A revalidate-and-recompute loop must consume the ENTIRE pending event queue per pass, or the input
set must be frozen before computation begins.** Consuming one event per pass is a livelock whenever the
arrival rate exceeds one event per pass. **Never disable a correctness check to force completion during an
incident**; if a check is disabled, every output produced afterwards is quarantined and reconciled before it
is treated as authoritative. A component livelocked on its input queue keeps accepting inputs it cannot
process, so its last computed output is arbitrarily stale: **assert the freshness of a computation's input
set at the point of commit.** A reconciliation failure raises an owned, escalating alert; withholding output
is not a response.

- **source:** **SEC Rel. 34-69655, NASDAQ / Facebook IPO** (incident 18 May 2012; order 29 May 2013), read
  verbatim. ¶19: *"only the first of those two cancellations was incorporated into a third price/volume
  calculation"*; ¶20: *"because the system was designed to perform a separate recalculation for each of those
  cancellations"*; ¶23 (validation-check lines removed from the failover); ¶26 (the removal is what let an
  11:11 input set price an 11:30 cross); ¶30 (the downstream Execution App then correctly refused to emit, and
  no one learned why for over two hours); ¶65 (the agreed remediation: close the order ports before the
  calculation, or take bursts of changes *"in one recalculation … rather than in multiple recalculations"*).
- **falsification recorded:** the draft said *"bound the retry count and fail to a defined state."* Read
  against the order; that is backwards: **a retry ceiling would have aborted the Facebook cross, not completed
  it.** The real defect is that the retry made **strictly less progress than the arrival rate**. The
  retry-ceiling clause is deleted, not softened.

**MS6 · MUST · body: halt means quiesce, at the smallest scope**

`halt ⇒ engine quiesced ∧ everything already produced delivered or explicitly voided`, at the smallest scope
that contains the breach. **Severing the transport is not a halt**: it abandons in-flight executions the
participants cannot see. Risk-reducing paths (`cancel`, `replace-down`, `close`, `flatten`, `settle`,
`reconcile`) stay callable while halted and are gated by a **different flag** than the risk-increasing path,
with a test that exercises them in the halted state. Give an invariant that can be **momentarily** false
during a known intermediate state, name the state and give it a bounded self-heal window before escalating; a check
that fires on a legitimately intermediate state is itself an availability bug. Confine exceptions to tasks;
never abort a process that holds unmanaged obligations.

- **source:** **TSE/JPX, 1 Oct 2020**, escalated to a whole-day halt because participants held undelivered
  fills and there were no rules for post-halt resumption; the LULD NMS Plan, a Trading Pause still executes
  the closing transaction; Ariane 501 R6, confine exceptions to tasks; Candea & Fox, *Crash-Only Software*,
  for the preconditions a crash-restart actually requires.
- **wording note:** "at the smallest scope that **provably** contains the breach" and "transiently
  falsifiable" were both cut. An agent will classify its own invariant as transient to avoid halting. "Name
  the intermediate state" is checkable; "transiently falsifiable" is a self-assessment.

**MS8 · MUST · reference: allocation**

Pro-rata allocation rounds down, therefore cannot allocate everything, and therefore **must never be the last
step**: define the leftover pass (FIFO by time priority, or the venue's documented rule) and assert
`Σ allocations == aggressing quantity` before any execution is emitted. **Execution prints at the RESTING
order's price, not the aggressor's.** Where aggressing quantity exceeds total resting quantity, the FIFO
exception applies. Note the quantity conventions that share one word: OUCH Cancel takes an **intended total**,
OUCH Replace takes a **chain-cumulative total** *"inclusive of previous executions and Self Match Prevention
decremented shares"*, and ITCH Modify carries a **decrement**: three conventions, one word.

- **source:** CME Globex Matching Algorithm Steps, Pro-Rata rounds fractional lots **down** and is never the
  last step, and *"if there is more quantity aggressing than available (resting), CME Globex uses FIFO as an
  exception to the algorithm in place."* Nasdaq OUCH 5.0 and TotalView-ITCH 5.0 for the three quantity
  conventions, with Nasdaq's own rationale that the cumulative convention *"inhibits the risk of
  double-liability throughout the order/replace chain"*.

**MS9 · MUST · front: SEAM S4 (exchange ↔ matching)**

*The counterpart half of this boundary is stated in `fin-exchange-integration`, from that side.*

**A client reconciles against the venue; a venue has nothing to reconcile against.** Where one process is both
a broker OMS that is the system of record for its clients' orders and simultaneously a client of the
exchange, **split the diff**: the client half is T2 and requires reconciliation by client order ID; the venue
half is T3 and requires order-by-order rejection on the last hop, a deterministic core, and deterministic
simulation. Do not let one tier declaration cover both halves.

- **source:** `architecture.md` §5.6 S4. **Knight Capital sat exactly on this boundary.**

**MS10 · MUST · front: validate the inbound order economically** *(new)*

**Reject any order whose price is more than a configured band away from that instrument's own reference
price**, and **reject or cancel-newest on a self-match**. The band is derived **per instrument from that
instrument's own reference price**, never from a cross-universe aggregate, and the same derivation applies on
every session-state code path (pre-market, auction, halt, continuous). Self-match prevention is applied at the
**account-family** level, not per strategy, and self-matched prints are filtered out of published volume.
A book without both prints the fat-finger trade and then owes everyone a bust process.

- **source:** **SEC Rel. 34-75331 (Goldman) ¶25/¶30**, the pre-market band's upper bound was 1.5× the
  highest closing price of *any* listed option ($3,090), so a $1 order in any name passed;
  17 C.F.R. § 240.15c3-5(c)(1)(ii) and the adopting release's price-collar expectation (fn 89, NYSE Arca Rule
  7.31(a) and Nasdaq Rule 4751); Nasdaq OUCH's AIQ modes ("Decrement both" / "Cancel oldest") for self-match
  prevention; **CFTC v. Coinbase, March 2021, $6.5M**, two internally operated programs *"matched orders with
  one another … resulting in trades between accounts owned by Coinbase"*, and that self-matched volume
  propagated into CME's Bitcoin Real Time Index, CoinMarketCap and the NYSE Bitcoin Index. Self-trades are an
  enforcement matter, not a vanity-metric matter.
- **gap recorded:** CME's Self-Match Prevention instruction set could not be retrieved; the suite's STP rules
  are grounded in Nasdaq AIQ ("Decrement both" / "Cancel oldest") and Coinbase only.

---

### 3.7 `fin-verification`: 10 rules (7 front)

| id | strength | placement |
|---|---|---|
| VF1 | MUST | front |
| VF2 | MUST | front |
| VF3 | MUST | front |
| VF4 | MUST | front |
| VF5 | MUST | front |
| VF6 | MUST | front |
| VF7 | MUST | body |
| VF8 | MUST | body |
| VF10 | SHOULD | reference |
| VF11 | MUST | front |

---

**VF1 · MUST · front: an invariant that does not run does not exist**

A reconciliation or invariant that exists as SQL in a comment, a docstring, or a *"worth running as a cron"*
note **is not a control and counts as absent**. Ship it as: a scheduled entrypoint with a named owner, a first
run that is not broken by un-backfilled history, an aged break bucket, and an alert destination read from a
**config key with no default that raises at import if unset**. State the cadence relative to the authority's
documented replication lag. The ship/no-ship question for any money-path change is **"does the invariant run
in production?"**, not "is the invariant written down?".

- **source:** Revolut: the only effective control was a partner bank's cash-position report;
  `architecture.md` P10. **Production code makes the justification empirical rather than moral:**
  nautilus_trader reconciles at startup only (`live/src/node/mod.rs:440`) and discards the continuous check
  with `let _ =` (`engine/mod.rs:1737`); no project read has a periodic conservation assertion that gates
  anything. The best platform computes it and throws it away.
- **wording note:** "page a human", "a channel with a named owner and an SLA" are things an agent cannot
  emit; they become comments, which is the exact failure this rule is about. A config key with no default
  that raises at import is the codeable substitute, and it fails loudly at the right time.

**VF2 · MUST · front: name the provenance before you assert**

Before writing an `assert`, `panic`, `abort`, `process::exit` or unhandled throw on a money path, **name the
provenance of every value in the asserted relation. If any of them crossed a network, a file, a config or a
clock; it is an operating error and needs a fail-closed guard, not an assertion.** A ledger at rest loses
nothing; a position at rest loses money and an unattended resting order keeps filling.

- **source:** **Ariane 5 Flight 501**, *"It was the decision to cease the processor operation which finally
  proved fatal"*, and *"the same software runs in both SRI units"*; Candea & Fox, *Crash-Only Software* §3.
  **The Polymarket overfill assertion (nautilus_trader #3221) looked like a programmer error and was an
  operating error: the violating number came from the venue**, with `last_qty=5.012345` against
  `quantity=5.000000`, producing `invalid order.leaves_qty: was -0.012345`. Assertion policy is decided by
  whether crashing leaves exposure unmanaged, and both correct answers exist in production code: TigerBeetle
  asserts in production at ~1 per 10.6 lines (487 assertions in 5,166 lines, `TIGER_STYLE.md:104-113`) because
  a ledger at rest loses nothing; nautilus_trader compiles its three `debug_assert!`s out of release because a
  panic in a process holding exposure is worse than a wrong number.
- **cut:** the draft's four-condition licence, *"a peer tolerates your absence"*, *"your state survives your
  death"*, *"the fault is uncorrelated across instances"*, *"no obligation accrues while you are down"*, is
  deleted. Four self-assessments an agent will grant itself, appended to a binding recipe, is textbook
  nuance-clause dilution: it converts consistent compliance into noisy compliance. The provenance test is the
  whole rule; the four conditions are an architecture question for a human, not a clause inside a binding
  rule.

**VF3 · MUST · front: the design-notes section is a list of claims**

Read the design-notes and docstring section **as a list of claims** and check each one against the code in the
same pass. For every property a comment asserts, *"the flush guarantees the row exists before the call"*,
*"the monotonic guard makes gaps impossible"*, *"sequence numbers are consumed on rejects so there are no
gaps"*, *"we only return NotOwner when the client does own something"*, either **point at the test that
proves it or delete the sentence**. An unverified claim in a comment is worse than silence: it is what makes
the defect survive review.

**VF4 · MUST · front: kill the process at every phase boundary**

An **atomic phase** is a set of local state mutations between two foreign state mutations, so the boundaries
are: **after the intent commit and before the call**; **after the call and before the outcome write**; **after
the outcome write and before the publish**. For each, `kill -9`, restart, run the recovery path, and assert
**exactly one external effect and one local record**. **Every field written ahead of the effect must be READ
by that test's recovery path**: a persisted `client_id` that the test never causes anything to read is not
covered.

- **source:** Brandur, atomic phases, *"atomic phases should be safely committed before initiating any
  foreign state mutation"*, and *"even foreign calls within your own infrastructure count! It's tempting to
  treat emitting records to Kafka as part of atomic operations… They're not."* The three boundaries in the rule
  are exactly the boundaries that definition produces.

**VF5 · MUST · front: the ambiguous-response test uses a delivered-then-failed request**

For every external value-moving call, write the ambiguous-response test: use a Toxiproxy `timeout` toxic
(*"stops all data from getting through, and closes the connection after timeout"*) or an equivalent stub so
the request **IS delivered upstream** and then fails. Assert three things: **no resubmission occurs**; the code
**queries by the identity it minted**; and **exactly one effect exists** afterwards. Run the mirror case where
the effect genuinely did not happen and assert the retry **does** occur. **A test that stubs the call to raise
before delivery tests nothing**: the failure mode is the delivered request.

- **source:** Toxiproxy's `timeout` toxic semantics, *"stops all data from getting through, and closes the
  connection after timeout"*, which is what makes the request delivered-then-failed rather than never sent;
  Binance, *"the execution status is UNKNOWN."* The negative case matters too: CCXT will re-POST a create-order
  on timeout if one option is enabled.

**VF6 · MUST · front: replay and permute every event consumer**

Take a recorded event stream, apply it in the recorded order, then apply **shuffled permutations of it with
duplicates and with a restart in the middle**, and assert final state is **byte-identical every time**. Assert
the conservation and idempotence properties **after EVERY step, not only at the end**. This is the only test
that catches an in-memory dedupe set, a watermark keyed on the live object, and an illegal transition in one
pass. Assert **generator coverage** explicitly: does the generator ever actually produce a duplicate at a
terminal state, or a restart between the effect and the outcome write?

- **source:** jqwik's `injectDuplicates()` and `Statistics.coverage` facilities, assert that your generator
  actually produces the scenarios you think it does; Elle on recoverability and traceability, and why *"blind
  writes to a register destroy history"*. The order-invariance form is implemented upstream: nautilus_trader's
  `test_avg_px_invariant_to_fill_arrival_order`.

**VF7 · MUST · body: record from production, replay with `record_mode="none"`**

Record HTTP and WebSocket fixtures from **production** endpoints and replay them with `record_mode="none"`, so
any unrecorded request fails the test. **Testnet proves protocol conformance and nothing else**: testnet order
books are independent, are wiped periodically, are missing endpoints, carry different filter thresholds, and
can receive breaking API changes before production. A dry-run is optimistic by construction: it exercises the
path where nothing goes wrong.

- **source:** VCR.py record modes, `none` is the mode that makes an unrecorded request an error. Binance's
  testnet documentation: market data and order books are *"independent and not synchronized with production"*,
  periodic resets wipe balances, orders and history, `/sapi` is unavailable, and filter thresholds may differ
  from production. ccxt#17545 records the inverted-fidelity hazard directly: **spot testnet had the breaking
  `MIN_NOTIONAL` → `NOTIONAL` change enabled before production did**. Freqtrade's own dry-run documentation
  lists assumptions that are optimistic by construction.

**VF8 · MUST · body: the tier, and what it gates**

**The tier sets the required evidence, never which rules apply.** Determine it from **Axis B and the observable
signal table in `architecture.md` §6**: does an external oracle exist that you can reconcile against? A bot can
(the exchange); a payments integrator can (the processor); **a matching engine, custodian or system-of-record
ledger cannot: it IS the oracle**, so reconciliation is unavailable as a safety net and the proof burden moves
before deployment into simulation. Apply the escalators as written. Publish the technique × tier matrix with an
explicit **actively-wasteful** column so "do I need DST here?" is a lookup, not a judgement.

**Axis A, blast radius per unit time, is narrative context for a human, not an input to the computation.**
"Max loss per erroneous action × actions per second" is unobservable from a diff and will be answered with a
fabricated number that then sets every downstream evidence requirement. Tier from Axis B and the signals.

- **source:** `architecture.md` §6 and its observable-signal table.

**VF10 · SHOULD · reference: know what each technique cannot find**

A **race detector will never find a double-spend**, because a lost update across two transactions is not a
data race, and it *"can't find races in code paths that are not executed."* **Line coverage does not show
whether money math asserts anything**, run mutation testing (PIT / mutmut / cargo-mutants / Stryker) on the
rounding, fee, allocation and PnL modules **only**, and require a high score there rather than chasing
coverage. **Deterministic simulation finds only the faults its author imagined and generates only the cases
its generator produces**: assert generator coverage explicitly and keep an external adversarial pass, because DST
does not subsume it. Loom and jcstress test memory-model correctness of lock-free data structures and will
tell you nothing about code using a database transaction or a mutex. **Run property tests in two modes**: a
fast derandomized suite in CI and a separate long-running randomized job, and commit every counterexample to
the repo as an `@example(...)`, because CI discards the example database and otherwise runs the same examples
forever.

- **source:** each technique's own documented limits. Go's race detector: *"the race detector only finds
  races that happen at runtime, so it can't find races in code paths that are not executed."* loom: *"any code
  that does not use loom's replacement types is invisible to loom."* jcstress: *"most of the tests are
  probabilistic, and require substantial time to catch all the cases."* PIT: *"line coverage… does not check
  that your tests are actually able to detect faults."* Hypothesis: `derandomize` and `print_blob` become True
  automatically in CI and the example database lives at `.hypothesis/examples`, which CI discards.
  **TigerBeetle ran 1024 cores of VOPR 24/7 and Jepsen still found two safety bugs plus seven crashes**: the
  VOPR corrupted whole sectors (always caught by checksums, always repaired) while Jepsen flipped single bits
  in padding (passed checksums, hit an assertion), and the fuzzer generated objects always consecutive in the
  index so the zig-zag merge join's probe path was never exercised. FoundationDB's counter-measures
  (`buggification`, and conditional coverage macros whose hit-counts reveal whether a scenario is generated at
  all) are the two things people skip.
- **absorbed:** the draft's separate VF9 (Hypothesis `derandomize` / `.hypothesis/examples` in CI) is folded
  into the last clause here. As a standalone rule it was a Python-library CI configuration note in a
  financial-correctness suite, the clearest "we found a fact and wanted to spend it" item in the spine.

**VF11 · MUST · front: prove the reconciliation detects** *(new)*

**Feed the reconciliation a known discrepancy and assert it produces the break record and fires the alert to
the routed channel.** VF1 requires the job to run; nothing else requires it to *detect*. The test seeds a
mismatch of a known amount on a known account, runs the job, and asserts: a `break` row exists with the right
`amount`, `currency`, `source_a`, `source_b`; the suspense posting keeps the trial balance balanced; and the
alert sink received exactly one message. Run it against a **freshly-migrated** database so an un-backfilled
opening balance fails the test rather than muting production.

- **source:** the generator-coverage doctrine, applied to a control rather than to a fuzzer, *"assert your
  generator actually produces the scenarios you think it does"*, plus the reconciliation contract LG3 states:
  a break record, a suspense posting that keeps the trial balance balanced, and a routed alert.
- **leverage:** the cheapest test in the suite. It protects VF1, LG3, EX12 and ON14 at once, and every broken
  reconciliation anyone has watched ship, shipped green.

---

## 4. Deliberately not shipped

Twenty-six entries. Twenty-four are correct, well-known practices that a competent engineer already applies
unaided, so writing them down would spend roughly half the suite's token budget to change nothing, and would
push the rules that *do* get skipped further from the top of the context window. One is deleted on wording
doctrine; one is a rule from the draft spine that did not survive editing. Each row says why it is out, and
what residue of it survives as a rule somewhere else.

### 4.1 Deleted because they are already applied unaided

| # | Deleted rule | Why it is not shipped | What survives, if anything |
|---:|---|---|---|
| 1 | Use `Decimal` / integer minor units / BigInt for money; never a binary float. | The single most-written rule in every finance style guide, and the one least likely to be violated by someone writing new money code today. | MC1 at reference, the ORM/driver type assertion, the decimal-context clause, and the transport-encoding carve-out, which are the parts a style guide never reaches, and MC2 (the currency residue). |
| 2 | Round price to `tickSize` and quantity to `stepSize`; validate `MIN_NOTIONAL`/`NOTIONAL` before sending. | Reliably done, and stating it again changes nothing. | A workflow step in the trading fast-path and the filter **property test** (EX19b), never a rule, because the property test finds the boundary cases the author cannot enumerate and the rule does not. |
| 3 | An HTTP 200 acknowledgement is not a fill; handle `PARTIALLY_FILLED`; size from `cummulativeQuoteQty`. | Basic venue mechanics, reliably handled. | Nothing. More Binance-basics prose inverts the token allocation the rest of this document argues for. |
| 4 | Handle `-1021` recvWindow clock skew, 429 and 418 with `Retry-After`. | Rate-limiter and clock-skew handling is fully internalised. | Nothing. |
| 5 | Replace read-compute-write on a balance with a single atomic conditional `UPDATE` or a `CHECK` constraint. | The pattern is well known, including the cross-app-server case. | Not the pattern but the constraint's **collateral damage**, LG4: the `CHECK` blocks the clawback. |
| 6 | Acquire locks in a deterministic order to avoid deadlock. | Standard practice. | Nothing. |
| 7 | Set the transaction isolation level explicitly; do not rely on bare READ COMMITTED for money. | The whole "SQL concurrency 101" family is applied without prompting. | The two residues that are *not*: the lock key must be deterministic across processes, and the lock must be held across the whole check→act section and lock the row the act mutates (MC12, ON13). |
| 8 | Guard concurrent refunds with a row lock or a unique constraint. | Standard practice. | Nothing. |
| 9 | A reversal is a new compensating entry; never mutate or delete the original record. | Reversal hygiene as a family is well understood and consistently applied. | LG12 ships the *enforcement*, a revoked `UPDATE`/`DELETE` grant, which is a different rule, and one that a journal declared "append-only" in a comment routinely fails. |
| 10 | Guard against reversing the same transaction twice. | Standard practice. | Only the mechanism that is usually missing: a uniqueness constraint on `reverses_transaction_id` with bidirectional links, folded into LG4. |
| 11 | Verify the webhook signature before processing the event. | The two most-repeated pieces of Stripe advice on the internet are fully internalised, while the semantics two inches away (re-fetch, ordering, disputes) are where the money is actually lost. Every token spent here is a token not spent there. | Nothing. |
| 12 | Dedupe webhook events on `event.id` with a unique index. | Done reliably, but done in a way that silently defeats recovery: an event you could not resolve is still committed to `processed_events`, so the provider never redelivers and the miss is permanent. | PAY4, the rewritten rule: mark processed **when applied**; unresolvable → DLQ. |
| 13 | The whole matching-engine cancel-semantics section (cancel/fill race, cancel remaining quantity only with correct `leaves_qty`, idempotent cancel, reject cancel on a terminal order, deterministic serialization vs matching, canceller ownership check, time priority preserved). | Cancel semantics are the best-understood part of writing a book. Durability and cross-path sequencing are not, and those ship (MS1, MS2). | Nothing. **This is the least confident deletion on the list**: it is the one place where the omitted material is genuinely intricate, and a reader building a venue should read MS1–MS10 as the floor, not the ceiling. |
| 14 | Weighted-average entry price on adds vs reduces; realize PnL only on the reducing portion; handle reduce-and-reverse through flat; get the short sign convention right. | Fill arithmetic is the part of position tracking people get right. Every systemic control *around* it (ordering, dedupe durability, fees, funding, reconciliation) is the part they do not. | Nothing. Write about ordering, reconciliation, fees and funding instead. |
| 15 | Wait N confirmations (or for the `finalized` tag) before crediting a deposit. | Everyone reaches for a depth. | ON11 only: the number is a magic constant, with no stated loss budget, no per-amount variation and no L1-vs-L2 unit distinction. |
| 16 | Write the deposit credit and the cursor advance in the same transaction. | Reliably done. | Nothing here; the failing half is **upstream**: nothing verifies the range was covered before the transaction opens (MC9, ON1). |
| 17 | Persist intent, then perform the effect, then record the outcome, crash-safely. | The pipeline *shape* is well known. | Only the three clauses that are not: **COMMIT** rather than flush, **never roll back** the intent on an ambiguous failure, and the recovery path must **consume** every write-ahead field (MC6). |
| 18 | Accept a caller-supplied idempotency id and enforce it with a unique constraint. | The table and the constraint get built. | The **derivation** rule (MC4: from a value that survives rollback, not a payload hash) and the **enforcement** rule (MC5: required parameter plus fingerprint comparison), neither of which follows from having the column. |
| 19 | Model available vs reserved balance and hold funds before broadcasting. | Standard practice. | LG7 only: intrinsic hold expiry enforced by the reader, and reservation-time invariant checking. |
| 20 | Re-fetch authoritative state from REST after a reconnect. | Everyone re-snapshots. What they do with the snapshot (gating, gap detection, dedupe) is where it breaks. | Nothing. Do not spend a word on "re-fetch"; spend them all on EX14 and EX15. |
| 21 | Validate the destination address: checksum, zero address, reject contract addresses. | Address validation is thorough by default. | Only the memo / destination-tag half (ON9), which is routinely absent entirely, and which makes funds un-creditable rather than merely misaddressed. |
| 22 | Chunk `eth_getLogs` requests by a maximum block range. | Chunking is present in essentially every indexer. | Only truncation detection, adaptive halving and provider failover (ON1). |
| 23 | Capture the commission and segregate it by asset; charge the network fee to the user; compute entry basis from the actual executed VWAP. | Each of these is the *easy half* of a compound requirement whose second half is consistently missing: capturing a fee is not booking it on both sides. | Only the hard halves: the non-quote-asset conversion (EX8), the counterparty and revenue legs for actual gas (LG1), and the round-trip fee gross-up (EX9). |
| 24 | Reject a non-positive amount; validate that `amount > 0`. | Universally applied. | Nothing. Note the interaction: MC13 must say *"prices may be negative, quantities may not"* precisely because this habit is already strong. |

### 4.2 Deleted on wording doctrine

**25. Any rule phrased as "consider…", "prefer…", "be careful with…", "handle errors carefully", or carrying a
nuance clause ("unless it matters", "where appropriate") or an exemption clause ("this does not apply to…").**

Two reasons, not one taste. **(1)** The failure this suite is aimed at is not that the risk goes unconsidered
it is that the risk is named accurately and then not implemented. A "consider" rule adds nothing to a
process that already deliberates and declines. **(2)** A nuance clause reopens the negotiation the rule was
written to close, and an exemption clause does not scope the way its author expects: *"this limit doesn't
apply to code blocks"* suppresses code blocks anyway. Every rule in this suite is therefore an artifact
requirement, and every real exception is expressed as its own conditional on an observable predicate.

### 4.3 Cut from the draft spine during this edit

**26. VF9: Hypothesis `derandomize` / `.hypothesis/examples` in CI.** A Python-library CI configuration note
in a financial-correctness suite: correct, mechanical, one-line fix, and entirely out of register. Folded into
VF10's last clause as a sentence rather than shipping as one of ninety rules. The underlying trap is real
(CI discards the example database, so every counterexample found is lost) and the remedy survives; the
standalone rule does not.

---

## 5. Wording doctrine for skill authors

This section is binding on anyone writing `skills/fin-*/SKILL.md`. It is about the *form* a rule takes, which
is a separate question from whether the rule is true.

### 5.1 Match the form to the failure

| Failure type | Form that works | Form that backfires |
|---|---|---|
| **Omitted element**: the thing simply is not in the output | A **REQUIRED SLOT** in a template the author is already filling | A prose reminder elsewhere, or a cross-skill name reference plus a verb the user never says |
| **Wrong-shaped output**: it complied but the shape is wrong | A **positive recipe**: state what the output IS, its parts, in order | A prohibition list. Naming the unwanted output tends to produce more of it, not less |
| **Discipline failure**: it knows better and does it anyway under pressure | A **prohibition**, plus a rationalization table and red flags | Soft guidance ("prefer…", "consider…") |
| **Conditional behaviour** | A conditional keyed to an **observable predicate** (a venue string, an error code, a column's existence, a documented retention window) | An unconditional rule with exemption clauses |

**The suite's dominant failure is an omitted element, not ignorance.** That is why every rule in §3 is phrased
as an **artifact requirement**, *"there is a call to X from Y"*, *"the schema carries column Z"*, *"the guard
is the write"*, *"the migration revokes the grant"*, and why prohibitions survive in exactly four places
where the failure is genuinely a discipline failure: *implemented, not described* (the prose TODO), and the
invariant-breach prohibitions inside **VF2**, **MS4** and **MS6**.

### 5.2 No nuance clauses, no exemption clauses, ever

*"Don't X unless it matters"* reopens the negotiation the rule was written to close, and turns consistent
compliance into noisy compliance. Exemption clauses do not scope: *"this limit doesn't apply to code blocks"*
suppresses code blocks anyway. Express a real exception as its own conditional on an observable predicate, or
restructure so the rule cannot reach the exempt part.

Three clauses were deleted from the draft spine on this ground alone, and it is worth naming them so the
pattern is recognisable:

- **VF2's four-condition licence** ("a peer tolerates your absence", "the fault is uncorrelated across
  instances", "no obligation accrues while you are down"), four self-assessments appended to a binding
  provenance test.
- **MS6's "provably contains" and "transiently falsifiable"**: an author will classify their own invariant as
  transient to avoid halting.
- **VF8's Axis A** ("max loss per erroneous action × actions per second"), unobservable from a diff, and the
  fabricated number then sets every downstream evidence requirement.

### 5.3 The required-output-slot pattern

An omitted element gets added by a slot, not by a pointer. Three slots exist in this suite, and they are
**not** duplication of the rules they enforce:

| Slot | Lives in | Enforces |
|---|---|---|
| The **`controls:` line** of the `FINANCIAL CHECK` (`<control> -> <file:line>`, or `UNRESOLVED: <control>, <why>`) | Every skill's `## Output` section, at every tier | The named-but-unimplemented control (*implemented, not described*) |
| The `controls:` line carrying `<control> -> <file:line> · <test name>` | Every skill's `## Output` section, at T2 and above | The same control, where someone else eats the error |
| The **verdict lines** (`ECONOMIC-DIFF: …` / `Financial tier: T<n> (inferred from: …)`) | Every skill's `## Output` section, and the routing block where it is installed | Gate invocation and tier declaration |
| The **two tests** (EX19a timeout-that-already-filled, EX19b filter property test) | `fin-exchange-integration`, front | The two failures that cost the most and are written the least |

EX19 and VF4/VF5/VF6 look adjacent and are not duplication: **EX19 is a slot in a template the author is
already filling; VF4/5/6 are the general contract for effects that are not exchange orders.** If the suite is
ever compressed, keep EX19: every competing design stranded it behind a cross-skill name reference plus a
verb ("test") this user never says.

### 5.4 RULE 0: the anti-paraphrase frame

> **In a money path, a named risk is implemented or the process refuses to start.** A comment, a design note,
> a "worth adding", a defined-but-uncalled function, or a `...` stub is a defect of the **same severity as the
> missing control itself**.

This is the framing that makes every other rule binding, and it is why every rule states what the code MUST
CONTAIN rather than what the author should think about.

Two corollaries the draft got wrong and this document fixes:

1. **"Ship `raise NotImplementedError`" is only a remedy on a path that is actually reached.** An uncalled
   function containing `NotImplementedError` satisfies the letter and instantiates the defect.
2. **The remedy needs a slot, not a sentence.** The draft gave prose to the highest-frequency failure and a
   slot to a rarer one. *Implemented, not described* now carries a slot at every tier: the `controls:` line
   below T2, the same line carrying a test name at T2 and above.

### 5.5 Placement, stated in the units the mechanism uses

- After auto-compaction, Claude Code re-attaches **only the first 5,000 tokens of each skill**, within a
  **25,000-token combined budget filled newest-first**, so older skills can be dropped entirely in a long
  session. `front` means "inside the first 5,000 tokens", and that is the only reason the distinction exists.
- Keep each `SKILL.md` **under 500 lines and 5,000 tokens**; past that, add a layer of hierarchy rather than
  compressing prose.
- **Budget: ≤120 words per front rule, ≤2,000 tokens of front rule text per skill.** The 50 front rules across
  seven skills come to ~8,000 tokens, ~1,150 per skill, leaving room for the workflow, the verdict template
  and the dispatch table.
- References are **one level deep**, each with a table of contents above 100 lines, reached by a dispatch row
  keyed on an observable predicate. **No `@file` references anywhere**: `@` force-loads and burns context.
- **Nothing a skill needs to be correct lives in a sibling.** Cross-references are by bare name with an
  explicit marker.

### 5.6 The default failure is that the skill never loads

A skill that is never invoked is worth exactly nothing, and a skill whose invocation depends on the user
saying the right word will not be invoked on the changes that need it most. Three consequences, all built into
how the rules are written. **The description is the routing surface**, so it states the economic situation
first and carries the literals as hints, because a description that is a keyword list only matches the
codebase that used those keywords. **A rule survives a rename**, so it fires on the situation rather than on a
spelling. And **the gate defaults to ON** with a mechanical predicate, because a gate whose cheapest compliant
emission is `ECONOMIC-DIFF: none` is one discretionary decision made once and early, by the party with the
incentive to decline.

The routing block in `AGENTS.md` reinforces the first of those and carries no rule. `architecture.md` §1.4
records the measurement that says passive context routes better than a description does, the cost of splitting
the rules across two artefacts, and the risk this position leaves open.

---

## 6. Open disagreements

Five places where reviewers split and this document had to decide. Each records the losing position, so a
future editor can reopen it with new evidence rather than from scratch.

### 6.1 "Terminal states are absorbing": falsified, and the falsification wins

**The draft asserted it in four places** (the old always-on block, MC10, EX15, PAY3). **nautilus_trader's source falsifies
it**: its exhaustive `(state, event)` table contains `(Canceled, Filled) => Filled`, annotated in source
`// Real world possibility`, plus a fifteenth status `Voided` for `(Filled, FillVoided)`. Code obeying the
draft rule **discards a real fill that crossed a cancel ack**.

**Decision: the falsification wins outright.** The replacement is enumerate-and-deny-by-default, with terminal
states accepting exactly the events by which the venue corrects a fact you already booked. The decisive detail
is that the ghost-order resurrection the draft rule was written to prevent is *also* fixed by the replacement:
it is a missing `_ => Err` arm, not a missing absorbing-terminal rule. The rule the evidence appeared to
support was never the rule the evidence implied. No dissent.

### 6.2 Venue facts: verify or cut, with no third option

Two reviewers disagreed on how much to salvage. One would keep the memo/tag table and the Binance endpoints on
the grounds that they are almost certainly right; one would cut anything unsourced.

**Decision: verify or cut.** XRP `DestinationTag` (uint32) and the four Stellar memo types were **verified
against primary documentation and kept, with their types**; Cosmos, TON and EOS were **cut** because they were
not. Binance's `/fapi/v2/positionRisk`, `/fapi/v2/account`, "the income endpoint" and `ORDER_TRADE_UPDATE.o.rp`
were **cut**: the `/v2/` paths do not exist in Binance's published documentation and the field could not be
verified anywhere. EX12 lost nothing important: its mechanism was never venue-specific.

The general principle, which should govern every future edit: **a front-placed rule may not carry an unsourced
factual payload.** A wrong error code or endpoint path destroys the reader's trust in every rule around it,
and the mechanism is almost always statable without it.

### 6.3 The time / business-date rule: rejected as a MUST, and why that is uncomfortable

One reviewer's single strongest addition was: every money-path timestamp is tz-aware UTC, and the business
date derives from a named cutoff in a named timezone, never `date.today()`. Their claim, *"a naive datetime
in a money path is the single most common defect I actually see"*, outranking half of MC1–MC16.

**Decision: rejected as a MUST or a front rule; shipped as MC17 at SHOULD/reference.** The reason is the
method this document is built on: every other rule here rests on a vendor document, a protocol specification,
a regulator's text, a standard, a named incident, or production source read at a pinned commit. This one rests
on none of them. Kleppmann's chapter on clocks supports the last sentence of MC17 and nothing else in it. A
rule with no such footing cannot be given a front slot without abandoning the method that justified excluding
two dozen other correct-sounding rules in §4.

This is the most uncomfortable call in the document, and it may well be wrong. If a named incident or a
vendor/regulator document turns up that pins the business-date failure, MC17 should be promoted on that
evidence rather than on assertion, and if none ever does, it should be deleted rather than left at SHOULD
forever.

### 6.4 Where the practitioner beat the primary sources: six additions kept

Six of the reviewer's seven "one missing rule per skill" were **kept**, because in each case a primary source
already existed and the spine had simply not used it:

- **EX20** (pre-trade limit check, colocated): 15c3-5(c)(1)(i)/(ii) verbatim, plus the adopting release's
  *"orders entered … rather than … executions obtained"*. The exchange skill's front budget was being spent on
  ClOrdID venue trivia while the first control every desk builds was absent.
- **PAY13** (processing fees are not refunded): Stripe's own words.
- **LG12** (append-only is a grant, not a comment): Modern Treasury and Square on enforcing immutability in
  the write path rather than by convention. A table *declared* append-only in a comment can still be updated.
- **MS10** (inbound price band + self-match prevention): SEC Rel. 34-75331 ¶25/¶30, and CFTC v. Coinbase.
  MS1–MS9 never validated an inbound order economically.
- **ON16** (queue health preconditions): kept on the nonce half, which go-ethereum's `legacypool` defaults
  state exactly; the gas half ships as a precondition **without a fabricated threshold**.
- **VF11** (prove the reconciliation detects): the cheapest test in the suite, and the one that catches a
  reconciliation that is broken on day one by un-backfilled opening balances.

The one rejected is §6.3. The pattern worth noting: **a practitioner's instinct is a hypothesis about which
primary source to go and find, and it was right six times out of seven.**

### 6.5 Near-duplicate rules: the "nothing else is duplicated" claim is retracted

The draft's notes asserted *"Nothing else in the suite is duplicated"* while shipping six near-duplicate pairs
in different wordings (MC10/EX15/PAY3, MC12/ON13, MC13/EX5, MC15/EX2, MC6/VF4, MC9/ON1) and running CI
divergence checks on only the four seams. A reader loading `fin-money-core` + `fin-exchange-integration` got
the same mechanism twice, in two wordings, with a green build.

**Decision: the duplication stays; the claim is retracted and the mechanism is extended.** `architecture.md`
D3 deliberately requires every domain skill to be fully useful with `fin-money-core` **not** loaded, and §5.7
resolves conflicts in the domain rule's favour because it is the same rule expressed in the code being
written. That design is right. The defect was never the duplication; it was the **unmanaged divergence**.

**Structural requirement for the skill authors:** every core↔domain pair above states the shared mechanism
once per skill, in that skill's own vocabulary, because D3 requires each domain skill to stand alone. The
defect to guard against is *unmanaged divergence*: two skills giving contradictory instructions for the same
mechanism. A conflict between two skills on a shared mechanism is a **suite defect to be reported, never a
judgement call**.

> **Not yet built.** A generation step (`shared/rules/<id>.md` as the single source, with CI failing on
> divergence) is the obvious mechanisation and is proposed in `docs/adoption.md` as an evaluation-gate item. It does not
> exist today, and no text in this repository is machine-generated. Divergence is currently caught by review.
