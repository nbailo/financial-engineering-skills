# Double-entry: the data model behind a journal that balances

The schemas real ledgers ship, and the tradeoff each one makes: how an account is represented, the two
internally-consistent sign conventions and why mixing them is the bug, normality and whose perspective it is
measured from, the chart of accounts including the external counterparty accounts that give boundary flows
their second leg, multi-currency as a dimension on one journal, and the primitives that let a group of postings
commit or fail as one. Drawn from TigerBeetle, Modern Treasury, Square Books, Uber's money orders,
Formance/Numscript and Beancount, read as source rather than as marketing.

## Contents

1. **Account representation** — four monotone counters versus a cached balance with a monotonic version.
2. **The transaction and its legs** — the four hard conditions; the three shapes an unbalanced journal takes.
3. **Sign conventions** — magnitude + direction versus uniformly-signed amount; why carrying both is the bug.
4. **Account normality and contra accounts** — the two balance formulas and the inverted-balance-sheet mistake.
5. **The chart of accounts** — suspense, clearing and residue as the *named* home for breaks.
6. **External counterparty accounts** — `world`, nostro, hot wallet; the worked five-leg withdrawal.
7. **Multi-leg atomicity** — `flags.linked` chain semantics and the fact that the linkage is not persisted.
8. **Batch results are per-event** — HTTP 200 on a 50-transfer batch is not "50 transfers succeeded".
9. **The id is the idempotency key** — eleven `exists_with_different_*` codes, `id_already_failed`, balancing.
10. **Two-phase transfers release the reservation in full.**
11. **Multi-currency** — functional vs transaction currency, FX gain/loss accounts, no cross-currency arithmetic.
12. **Schema sketch** — the constraints and grants that actually enforce the invariants, in Postgres.
13. **Tenancy and identity**, and **14 · control plane versus data plane**.

## 1 · Account representation

Two designs are in production and both are defensible. What is not defensible is a bare `balance` column a
service reads, adjusts in application memory, and writes back.

**Monotone counters (TigerBeetle).** An account stores four unsigned 128-bit counters and no net balance:
`debits_pending`, `debits_posted`, `credits_pending`, `credits_posted` (docs.tigerbeetle.com/reference/account).
Balance is derived at read time — `debits_posted − credits_posted` for a debit-normal account,
`credits_posted − debits_posted` for a credit-normal one. Counters are commutative and monotone, so two
concurrent transfers touching the same account never read a net figure before writing one. The cluster-level
invariant that falls out is checkable in one query: `Σ debits_pending == Σ credits_pending` and
`Σ debits_posted == Σ credits_posted`, per `ledger`.

**Cached net balance with a version (Square Books).** Books caches the balance on the `books` row so a balance
read is "a single row" rather than a group-by, updates it **inside the same Cloud Spanner transaction as the
journal entry**, and guards it with a monotonic version counter. Modern Treasury does the same shape with an
explicit optimistic-lock row: the caller passes the expected `lock_version` per entry and a mismatch rolls the
whole transaction back — surfaced on the ledger transaction as the archive reason `balance_lock_failure`.
The failure that kills this design is `INSERT INTO entries …; COMMIT;` followed by a separate
`UPDATE balances`: a crash between them, or a retry of one half, leaves permanent drift and no error.

## 2 · The transaction and its legs

Formance states the invariant as hard conditions (formance.com/blog/engineering/defining-double-entry). Add
the currency clause from Modern Treasury, which validates double-entry *per currency*, and you get four:

(1) at least one debit leg; (2) at least one credit leg; (3) at least two distinct accounts — TigerBeetle
enforces the primitive form, `debit_account_id != credit_account_id`; (4) `Σ debits == Σ credits` **for each
currency present in the group**, independently. Uber validates the same property pre-commit — "all entries in
any money order sum to zero" — before any ledger write; Beancount: "the sum of all the postings of a
transaction must equal zero"; Square: "all transactions must balance to 0."

**Where this is enforced decides whether it can be violated.** A downstream audit job that finds an unbalanced
group finds it after the money moved. The entrypoint takes a *set* of legs and returns
`UnbalancedGroup { currency, delta }` in the same transaction as the write; no code path then produces an
unbalanced state, so nothing is left for a runtime "do the books balance" check to catch.

**The three shapes an unbalanced journal takes.** Ledger code that names itself double-entry and enforces the
balance condition nowhere fails in three recognisable ways:

| shape | what was written | why the group does not net to zero |
|---|---|---|
| single leg | one row: the user's account, the amount | the counterparty leg — hot wallet, bank, `world` — was simply absent; value appeared from nowhere at the boundary |
| missing counterparty + missing revenue | user debited `amount + fee`; user's balance correct | nothing credited the fee to a revenue account and nothing credited the hot wallet; the group is short by `amount + fee` |
| `-2 × amount` | two rows for one settlement, both carrying a negative amount | in a signed-`amount` column the sign was applied twice, or both legs were written from the payer's perspective — see §3. The table's own comment declared it "append-only double-entry" |

The third is the diagnostic one: `-2 × amount` is not a rounding artefact, it is arithmetically only reachable
by writing two legs with the same sign — which is what a single signed `amount` column invites.

**The assert that ships.** A copyable `assert sum(l.amount for l in legs) == 0` passes on `+100 JPY, −100 USD`;
the currency clause goes *inside*:

```python
assert all(sum(l.amount for l in legs if l.currency == c) == 0
           for c in {l.currency for l in legs}), f"unbalanced group {group_id}"
```

## 3 · Sign conventions

Two conventions are internally consistent and both satisfy §2. Pick one and make the other unrepresentable.

| | Convention A — magnitude + direction | Convention B — uniformly signed |
|---|---|---|
| column shape | `amount NUMERIC CHECK (amount > 0)` + `direction` enum, or `source`/`destination` account ids | one signed `amount`; debits positive, credits negative |
| who ships it | TigerBeetle (`debit_account_id` / `credit_account_id`), Modern Treasury, Formance (`{amount, asset, source, destination}`) | Square Books ("debits positive, credits negative, uniformly — not debit-normal/credit-normal"), Beancount ("it's much easier to just always add up the numbers") |
| balance query | `SUM(CASE direction WHEN 'debit' THEN amount ELSE -amount END)` | `SUM(amount)` |
| group check | `SUM(debits) = SUM(credits)` per currency | `SUM(amount) = 0` per currency |
| cost | every reader must apply the direction; forgetting it inverts a balance silently | the sign is not the account's normality, so a naive reader thinks a credit-normal account is negative |

**The footgun is neither signs nor directions — it is carrying both.** A row with `amount = -500,
direction = 'credit'` and a row with `amount = 500, direction = 'credit'` both "work" in the service that wrote
them, and the aggregate across services is wrong with no failing query anywhere: two sources of truth for one
fact, on one row, with nothing tying them together.

If the table must carry both — a migration in progress, a wire format you do not own — the redundancy is made
non-authoritative by a constraint, not by a code review: `CHECK ((direction = 'debit' AND amount > 0) OR
(direction = 'credit' AND amount < 0))`.

Convention B says nothing about normality (§4). Beancount handles that by signing from the *owner's*
perspective and letting the accounting equation absorb it — `A + L + E + X + I = 0`. Adopt B and you must
adopt that framing too, or liability accounts read as negative in every report and someone "fixes" them.

## 4 · Account normality and contra accounts

Normality is a column on the account, not a global constant, and it selects the balance formula:

| account type | normality | balance | increases on | in a money-moving system |
|---|---|---|---|---|
| Asset | debit | `debits − credits` | debit | hot wallet, bank/nostro, processor receivable, custodian |
| Liability | credit | `credits − debits` | credit | **customer wallet balances**, unearned fees, payable-to-partner |
| Equity | credit | `credits − debits` | credit | `Equity:Opening-Balances`, retained earnings |
| Income / Revenue | credit | `credits − debits` | credit | trading fees, withdrawal fees, FX spread, interest income |
| Expense | debit | `debits − credits` | debit | network/gas fees, processor fees, interest expense, write-offs |

GnuCash states the equation as `Assets − Liabilities = Equity + (Income − Expenses)`; Beancount as
`A + L + E + X + I = 0` with signed numbers — same statement under a sign flip.

**Normality is perspective-dependent, and this is where the balance sheet inverts.** TigerBeetle:
"the type of account depends on whose perspective you are doing the accounting from"
(docs.tigerbeetle.com/coding/financial-accounting). A customer deposit is the *customer's* asset and *your*
liability. A platform that models user wallets as asset accounts on its own books passes every double-entry
check — the groups still balance — and reports customer money as its own. Nothing in §2 catches it; only a
chart-of-accounts review, or a trial balance whose sides are typed, does.

**A contra account is a normality inversion paired to a parent, not a new mechanism.** `Allowance for doubtful
accounts` is credit-normal under a debit-normal `Accounts receivable`; `Contra-revenue: refunds and chargebacks`
is debit-normal under credit-normal revenue. The child carries the opposite `normality`, the roll-up subtracts
it from the parent, and postings to it obey §2 like any other leg. Use one instead of debiting the parent so
gross and net stay separately reportable — "how much did we book" and "how much did we give back" both stay
answerable.

## 5 · The chart of accounts

Name accounts as a path, so a prefix scan is a roll-up: `liability:customer:{user_id}:USD`,
`asset:hot_wallet:ethereum:USDC`, `revenue:fees:withdrawal:USD`, `expense:network:ethereum:ETH`. Monzo's ledger
address is the tuple `(legal_entity, namespace, name, currency, account_id)` — the same idea, separated.

Beyond the five types, three roles carry the operational weight:

| role | example path | steady state | what it buys |
|---|---|---|---|
| clearing / in-transit | `clearing:processor:stripe:USD`, `clearing:onchain:pending_finality:USDC` | **zero** | Stripe's invariant — "terminal (nonclearing) reservoirs are full, and intermediate (clearing) pipes are empty" — makes reconciliation a query: a nonzero clearing balance past its settlement window *is* the detector |
| suspense | `suspense:recon:{source}:USD` | zero, aged | the named home for a break you cannot yet attribute; the discrepancy posts here so the **trial balance still balances** while the break is open. Fed FAM §4.50's **Difference account** absorbs "an out-of-balance condition resulting from the normal operation of a department" and is swept monthly |
| residue | `expense:rounding_residue:USD`, `revenue:fx_residue:USD` | small, monitored | minor units a split or conversion cannot allocate go somewhere by name. Numscript allocates split remainders deterministically top-down the list, so there are "no invisible fractions or mystery money" |

A nullable `discrepancy_cents` column and a log line are not a suspense account: they leave the books
unbalanced and the break invisible to every report. Give each clearing and suspense account an owner and an
aging policy at creation — an expected-zero account with no owner becomes a permanent residue bucket.

## 6 · External counterparty accounts

Money crossing your system boundary still needs two legs, and the counterparty account *is* the second one.
Formance ships `world` as the built-in external account funds originate from and drain to; banking systems use
a **nostro** (your money, held at their bank) per correspondent; card systems a network-settlement account per
scheme; a crypto system a hot-wallet asset account per (chain, asset). Without them you get the control
experiment's first shape: a single leg, and value that appears from nowhere.

**Worked example — withdrawal of 100 USDC, 1.50 USDC fee charged to the user, 0.000318 ETH of gas spent from
the hot wallet.** Five legs across two currency dimensions; USDC ledger scale 6, ETH ledger 18; minor units.

| # | account | dr/cr | currency | amount (minor) |
|---|---|---|---|---|
| 1 | `liability:customer:U123:USDC` | DR | USDC/6 | 101_500_000 |
| 2 | `asset:hot_wallet:ethereum:USDC` | CR | USDC/6 | 100_000_000 |
| 3 | `revenue:fees:withdrawal:USDC` | CR | USDC/6 | 1_500_000 |
| 4 | `asset:hot_wallet:ethereum:ETH` | CR | ETH/18 | 318_000_000_000_000 |
| 5 | `expense:network:ethereum:ETH` | DR | ETH/18 | 318_000_000_000_000 |

`Σ USDC: 101_500_000 dr − 101_500_000 cr = 0.` `Σ ETH: 318e12 dr − 318e12 cr = 0.`

Four things this makes concrete. (i) The user's liability falls by principal **and** fee — one debit, not two
postings written at different times. (ii) The fee is *revenue*, credited where a P&L can read it; held in the
same account as principal, or netted against the hot wallet, it is lost. (iii) Gas is an **expense you actually
paid**, denominated in ETH — it does not appear in the USDC group and is never converted into USDC on the
journal (§11). (iv) Drop any row and the group is unbalanced: the entrypoint rejects it rather than a
reconciliation discovering it on Tuesday. If the primitive forbids cross-`ledger` transfers (TigerBeetle does:
both accounts must share the same `ledger`), legs 1–3 and 4–5 are two transfers on two ledgers, tied by a
shared correlation id in `user_data_128` — and by §7's chaining if they must commit as one.

## 7 · Multi-leg atomicity

A group of postings not committed by one operation is not a group. Two primitives exist.

**Linked chains (TigerBeetle).** `flags.linked` links an event to the next; the chain ends at the first event
*without* the flag. Semantics that bite:

- A request whose **last** event still has `linked` set fails with `linked_event_chain_open`.
- The first failure in a chain returns its real error code; every other event in that chain returns
  `linked_event_failed`. Reading only the first non-`ok` result tells you nothing about the cause.
- **The linkage is not persisted** — it is an execution-time construct. If the business relationship between
  the legs must survive the request (for a journal group it must), re-encode it in `user_data_128` or `code`
  on every member. Code that reconstructs a settlement group by looking for `linked` afterwards finds nothing.

**One script, all postings (Numscript).** Formance's DSL expresses the whole flow — splits, fees, cascading
funding sources with caps and overdraft rules — as one script that commits all postings or none, using integer
math only with built-in rounding rules. No intermediate state for a crash to leave behind, no compensating
action to get wrong. The relational equivalent: all legs in one `INSERT … SELECT` from a VALUES list, inside
one database transaction, behind §12's entrypoint. Never one `INSERT` per leg with a commit between them.

## 8 · Batch results are per-event

TigerBeetle's Requests documentation draws the distinction that costs money: "All events within a request
batch are committed, or none are… this does **not** mean that all of the events in a batch will succeed, or
that all will fail. Events succeed or fail independently unless they are explicitly linked." Durability is
atomic; business outcome is not.

```python
results = client.create_transfers(batch)                     # len(batch) == 50
ok = {r.index for r in results
      if r.result in (CreateTransferResult.OK, CreateTransferResult.EXISTS)}
failed = [(batch[r.index].id, r.result) for r in results if r.index not in ok]
if failed: raise PostingRejected(failed)                     # do NOT mark all 50 settled
```

`if resp.status_code == 200: mark_all_settled()` sets a settled flag on transfers rejected for
`exceeds_credits`. `exists` is a *success* here and must be in the accepting set (§9); and events within a
request execute in sequence with each one's effects visible to the next, so a later event may legitimately
depend on an earlier one having landed.

## 9 · The id is the idempotency key

TigerBeetle has no separate idempotency header: the user-supplied `id` on the transfer *is* the cluster-wide
idempotency key. The client generates it, **persists it locally before submission**, and reuses it verbatim on
every retry across timeouts, restarts and redeploys; the recommended scheme is a 48-bit millisecond timestamp
concatenated with 80 random bits, so ids are sortable without a central oracle. Minting a fresh id inside the
retry loop is the whole bug. On a hit the payload is compared **field by field**, with eleven distinct result
codes rather than one opaque conflict (`src/tigerbeetle.zig:236-247`):

| code | value | code | value |
|---|---|---|---|
| `exists_with_different_flags` | 36 | `exists_with_different_user_data_128` | 41 |
| `exists_with_different_debit_account_id` | 37 | `exists_with_different_user_data_64` | 42 |
| `exists_with_different_credit_account_id` | 38 | `exists_with_different_user_data_32` | 43 |
| `exists_with_different_amount` | 39 | `exists_with_different_timeout` | 44 |
| `exists_with_different_pending_id` | 40 | `exists_with_different_code` | 45 |
| `exists_with_different_ledger` | 67 | `exists` | 46 |

Comparison order is documented in source (`src/state_machine.zig:3990-4045`): flags are compared **first**,
because "the flags change the behavior of the remaining comparisons". Two refinements you will not derive:

- **`id_already_failed = 68`** (`src/tigerbeetle.zig:252`, returned at `src/state_machine.zig:3736` on a
  `.found_orphaned` lookup). An id whose first attempt failed for a *transient* reason — account not found,
  `exceeds_credits`, account already closed — is permanently poisoned; the negative outcome is durable and you
  must mint a new id. Contrast Stripe, where a pre-execution failure saves nothing and the key stays retryable.
  Genuinely different contracts; state which one your API has.
- **The balancing-transfer exception** (`src/state_machine.zig:4016-4030`). For `balancing_debit` /
  `balancing_credit` the committed amount is usually *less* than requested, so a retry carrying the original
  request fails a naive equality check. The code compares `t.amount < e.amount` for balancing transfers and
  `t.amount != e.amount` otherwise: **the fingerprint is over the request as the client meant it, not over the
  committed result.** Every "transfer up to X" operation needs this.

The homegrown anti-pattern is `INSERT INTO entries(idempotency_key, …) ON CONFLICT DO NOTHING` then "if not
inserted, return the cached success": it silently accepts a *different* request under a used key — a different
amount, a different destination — and reports success for a posting that never happened.

## 10 · Two-phase transfers release the reservation in full

Stated here only because it is the conservation half of the two-phase mechanism.
`src/state_machine.zig:4240-4252` decrements the reservation by `p.amount` — the **pending transfer's own**
amount — never by the amount named on the resolving request:

```zig
dr_account_new.debits_pending  -= p.amount;      // always the reservation, in full
cr_account_new.credits_pending -= p.amount;
if (t.flags.post_pending_transfer) { assert(amount_actual <= p.amount);  … }
if (t.flags.void_pending_transfer) { assert(amount_actual == p.amount);  … }
```

Post less than reserved and the remainder is restored automatically; post more and the request is rejected
(`exceeds_pending_transfer_amount`) rather than clamped; void must be exact. The resolving transfer is a *new*
record with its own id and a `pending_id` back-reference, resolving exactly once —
`pending_transfer_already_posted = 33`, `pending_transfer_already_voided = 34`.

## 11 · Multi-currency

**One journal with a currency dimension** — not one table or one database per currency, which are schemas
people spend a year unwinding. The dimension carries currency *and scale* and is immutable: TigerBeetle's
`ledger` (u32, non-zero) partitions accounts by asset and its asset scale maps the smallest useful fractional
unit to 1 (USD→2, JPY→0, KWD→3); "asset scales cannot be changed after account creation without migrating to a
new ledger". Formance encodes scale into the asset identifier itself: `USD/2`. An integer amount without its
`(currency, scale)` pair is meaningless.

**Functional versus transaction currency.** IAS 21 defines the **functional currency** as that of the primary
economic environment in which the entity operates, and allows a different **presentation currency** for
reporting; the *transaction currency* is the one the deal was struck in and is what goes on the leg. Initial
recognition is at the spot rate on the transaction date. At each reporting date **monetary items** (cash,
receivables, payables, customer balances) are remeasured at the closing rate while **non-monetary items** stay
at their historical rate, with the exchange differences generally going to profit or loss.

**FX gain/loss accounts — two of them, and they are not the same account.**
`revenue:fx_spread:{pair}` is the margin you charged, booked as its own posting: TigerBeetle's
currency-exchange recipe requires the spread be a *separate* transfer rather than baked into the rate, because
rate-plus-spread merged into one number "cannot be derived" afterwards. `income/expense:fx_revaluation:{ccy}`
is the difference between the historical rate a monetary balance was booked at and the closing rate. Selinger's
currency-trading account is the clean construction: an account holding a multi-currency expression such as
`USD 100 − CAD 120` makes unrealised gain/loss fall out of revaluation with no adjusting entries, and realised
gain is recognised on disposal — "the purpose of a currency trading account is not to perform conversions, but
to calculate gains and losses."

**The ban.** No expression adds, subtracts or compares two amounts of different currencies, and no rate is
multiplied in place onto a stored balance. `SUM(amount_minor)` without `GROUP BY currency` yields 2000 of
nothing when it sees 1000 JPY and 1000 USD, and it does not raise. An FX movement is a *balanced transaction
per currency* — source-currency legs netting to zero, target-currency legs netting to zero, joined through
liquidity/trading accounts — with rate, provenance, side and pivot on the transaction, and the conversion
residue posted to a named residue account.

## 12 · Schema sketch

Postgres. Every invariant below is enforced by a constraint or a grant, not by the application remembering.

```sql
CREATE TYPE entry_direction AS ENUM ('debit', 'credit');

CREATE TABLE accounts (
  id            uuid PRIMARY KEY,
  path          text NOT NULL UNIQUE,                     -- 'liability:customer:U123:USDC'
  account_type  text NOT NULL
                CHECK (account_type IN ('asset','liability','equity','income','expense')),
  normality     entry_direction NOT NULL,
  currency      text NOT NULL,                            -- ISO 4217 or asset symbol
  scale         smallint NOT NULL CHECK (scale BETWEEN 0 AND 18),
  is_contra     boolean NOT NULL DEFAULT false,
  parent_id     uuid REFERENCES accounts(id),
  CHECK ( (account_type IN ('asset','expense')       AND normality = 'debit')
       OR (account_type IN ('liability','equity','income') AND normality = 'credit')
       OR is_contra )                                     -- contra inverts, deliberately
);

CREATE TABLE journal_groups (
  id               uuid PRIMARY KEY,
  idempotency_key  text NOT NULL UNIQUE,                  -- required, never nullable
  effective_at     timestamptz NOT NULL,                  -- economic time, ≠ posted_at
  posted_at        timestamptz,
  status           text NOT NULL CHECK (status IN ('pending','posted','archived'))
);

CREATE TABLE entries (
  id          bigserial PRIMARY KEY,
  group_id    uuid NOT NULL REFERENCES journal_groups(id),
  account_id  uuid NOT NULL REFERENCES accounts(id),
  direction   entry_direction NOT NULL,
  amount      numeric(38,0) NOT NULL CHECK (amount > 0),  -- minor units, magnitude only
  currency    text NOT NULL,  scale smallint NOT NULL
);
CREATE INDEX ON entries (group_id);  CREATE INDEX ON entries (account_id, id);
```

- `amount numeric(38,0) CHECK (amount > 0)` plus `direction` is Convention A — no sign can disagree with the
  direction, so §3's `-2 × amount` shape is unrepresentable. Never `double precision`, and never Postgres's
  `money` type: its output is `lc_monetary`-dependent, its fractional precision is fixed by locale rather than
  by the value, and `money / integer` truncates toward zero (postgresql.org/docs/current/datatype-money.html).
- `currency`/`scale` denormalised onto `entries` so the per-currency check needs no join, with a trigger or FK
  asserting they match the account's. An entry whose currency differs from its account's is a defect.
- `idempotency_key` on the *group*, not the entry. Uniqueness alone is not idempotency: on a conflict the
  entrypoint loads the stored group and compares `account_id`, `direction`, `amount`, `currency` leg by leg,
  returning a per-field mismatch (§9), not a bare success.

The group invariant, as a deferred constraint trigger, so a multi-statement insert is legal but an unbalanced
commit is not:

```sql
CREATE FUNCTION assert_group_balanced() RETURNS trigger AS $$
DECLARE bad record;
BEGIN
  SELECT currency, SUM(CASE direction WHEN 'debit' THEN amount ELSE -amount END) AS delta
    INTO bad
    FROM entries WHERE group_id = NEW.group_id GROUP BY currency
  HAVING SUM(CASE direction WHEN 'debit' THEN amount ELSE -amount END) <> 0  -- balances
      OR COUNT(DISTINCT account_id) < 2                                      -- ≥2 accounts
      OR COUNT(*) FILTER (WHERE direction = 'debit')  = 0                    -- ≥1 debit
      OR COUNT(*) FILTER (WHERE direction = 'credit') = 0                    -- ≥1 credit
   LIMIT 1;
  IF FOUND THEN RAISE EXCEPTION 'unbalanced group % in %: delta=%',
    NEW.group_id, bad.currency, bad.delta USING ERRCODE = '23514'; END IF;
  RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER entries_group_balanced AFTER INSERT ON entries
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION assert_group_balanced();
```

Append-only is a grant, not a comment, and the entrypoint is the only writer:

```sql
REVOKE UPDATE, DELETE, INSERT ON entries FROM app_role;
GRANT EXECUTE ON FUNCTION post_group(jsonb) TO app_role;   -- SECURITY DEFINER
```

Honest limits. The deferred trigger fires once per inserted row at commit and re-scans the group each time —
fine at four legs, measurable at four hundred; check only on the group's first row, or move the check inside
`post_group` and keep the trigger as the backstop for anything that later gets a direct grant. A superuser
setting `session_replication_role = replica` disables the trigger, so the revoked grant is the load-bearing
control. And this schema enforces conservation *within* a group; whether the group describes reality is
reconciliation's job and takes a suspense posting (§5).

## 13 · Tenancy and identity, and 14 · control plane versus data plane

TigerBeetle's account carries `ledger` (u32, non-zero — the currency/asset partition), `code` (u16, non-zero —
the account type, i.e. your chart-of-accounts class as an integer), and `user_data_128` / `_64` / `_32` for
correlation. String↔code mappings live in the business database; the ledger stores the code. TigerBeetle
itself has **no authentication** — the API layer in front of it is where authorization happens. The rule that
shapes the boundary: **"initiating a transfer should not require fetching metadata from the
general purpose database"** (docs.tigerbeetle.com/coding/system-architecture). The posting path takes account
ids and integers. If it must look up a KYC status, a fee schedule or a currency name mid-posting, you have
coupled the money path's availability to the CRM's and given yourself a read whose staleness changes an
economic outcome. Resolve that *before* constructing the group.

The same split downstream: the ledger of record is OLTP and is not a reporting database. Formance ships ledger
logs to replica stores for OLAP querying; Square built Books explicitly to escape group-by aggregations on the
transactional path. A dashboard running `SELECT SUM(amount) FROM entries GROUP BY account_id, currency` over
full history scans the same rows an authorization is trying to read — and Monzo's operational note is that
delayed balance reads force card **stand-in processing**, risking unauthorised negative balances. Write-path
latency is a correctness property here, not a UX one. Replicate the journal for trial balance, statements and
month-end; keep the write path serving the balanced-set entrypoint and the reads that authorise.
