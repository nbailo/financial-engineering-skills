# Provider coverage and evidence state

Coverage is not uniform across venues and processors, and a table that implies it is gets an integration
trusted further than the evidence goes. This file answers two separate questions, and the difference between
them is the part to read before you trust a row:

1. **Coverage.** How much venue-specific material exists at all.
2. **Evidence state.** How fresh the sourcing behind that material is.

## 1. Coverage levels

- **deep / verified**: a dedicated reference for that venue, written from the venue's own documentation,
  carrying its field names, its error codes, and the named regression assertions the reference requires you
  to write.
- **generic**: covered only by the cross-venue invariants and the ccxt or FIX references. Nothing
  venue-specific is asserted.
- **experimental**: the public surface exists and some of it is verified, but the current semantics are
  incomplete and the surface is version-sensitive.
- **unverified**: named as a possible integration and **not** advertised as supported.

A level says how much venue-specific material exists and what it was written from. It does not say when the
sourcing was last re-read: most of the venue files below carry `verified_at: not established`, which section
2 explains. Read the level and the evidence state together, or you will trust a row for the wrong reason.

| Provider | Level | What backs it |
|---|---|---|
| Binance (spot, USD-M, COIN-M) | deep / verified | Dedicated references for orders and error codes, filters, private streams, and the depth-to-snapshot join |
| OKX | deep / verified | Dedicated order and order-book references, plus the cross-venue divergence matrix |
| Bybit | deep / verified | Dedicated order and order-book references, plus the divergence matrix |
| Kraken | deep / verified | Dedicated order and order-book references, including the CRC32 book checksum |
| Coinbase Advanced Trade | deep / verified | Dedicated reference, including the re-POST semantics that differ from the other venues in the set |
| Deribit | deep / verified | Dedicated reference: order identity, recovery endpoints, price validity as significant figures |
| Hyperliquid spot and perpetuals | deep / verified | Dedicated reference: `cloid`, nonce semantics, positions, funding, venue-originated liquidation and ADL as client-observed facts |
| Polymarket CLOB V2 | deep / verified | Dedicated reference against production V2: the signed-struct field changes, pUSD collateral, protocol-selected fees, tick discovery |
| Kalshi | deep / verified | Dedicated reference: direction vocabularies, fixed-point migration, fee authority chain, market lifecycle, sharding |
| Limitless | deep / verified | Dedicated reference: auth modes and scopes, the delegated and EOA order surfaces, `orderEvent` sources, redemption |
| Hyperliquid outcome markets | experimental | Four `outcomeMeta` field names and the `settledOutcome` request are verified; the naming, asset ids and coin encoding are not, and are labelled unverified in the reference |
| Alpaca | generic | The cross-venue invariants and the ccxt reference. It appears in the skill body as a routing literal, not as a verified provider row: no primary-source pass was made on its current API for this release |
| Any venue reached through ccxt | generic | The ccxt reference: its precision modes, its retry funnel, and what a unified type flattens |
| Any FIX venue | generic | The FIX reference: `PossDupFlag`, `PossResend`, `OrigClOrdID`, resend semantics |
| Everything else | unverified | Nothing is listed here in this release. A venue arrives at `generic` or better with a reference, or it is not named at all |

Prediction markets are covered inside `fin-exchange-integration`, as references beside the spot and
derivatives venues, not as a separate skill. A prediction-market bot loads the same skill a Binance bot
loads, and picks up `prediction-market-core.md` plus the venue file its literals name.

**What no level implies.** No level, `deep / verified` included, means the suite ships recorded API captures.
The fixture an assertion runs against is captured from your own account, and the references say which call to
capture and why a hand-written one proves nothing.

## 2. Evidence state

Every provider and protocol reference opens with a provenance block: provider, surface, version,
`verified_at`, the source URLs, what was verified, an explicit list of what was **not**, and a
`revalidate_when` trigger that names the change which would invalidate the file.

Three honest states, and a file is always in exactly one of them:

- **Currently revalidated.** The block carries a `verified_at` date, and on that date a person opened the
  cited sources and confirmed the file still describes them.
- **Sourced but revalidation pending.** The file was written from the vendor documentation it cites inline,
  and nobody has re-read that documentation since. `verified_at: not established` says exactly that, rather
  than showing a date nobody earned.
- **Illustrative or historical only.** Material kept because it shows a failure mode, not because it
  describes a live API: everything under `incidents/`, and any example labelled as historical in place.
  Nothing here is a current description of a provider surface, and no rule may be derived from it without a
  current source. A reference enters this state by declaring `classification: illustrative` in its own
  provenance block, beside an `unverified:` line saying what it is not claiming. The state is never
  inferred, because inferring it would let a file opt out of revalidation by going quiet, and it cannot be
  claimed in the other direction: `classification: revalidated` without a current `verified_at` behind it is
  reported in the state its evidence supports.

The states are per file and they move. To see which state each reference is in right now:

```bash
python3 scripts/validate.py --provenance-report
python3 scripts/validate.py --provenance-report --max-age-days 90
```

The report puts every reference in one of the three states above, counts them, prints the date and the age
of each, and prints the `revalidate_when` trigger for exactly the files that are pending, so a maintainer can
tell whether the trigger has already fired. **It exits non-zero when a required source is past its
revalidation trigger**, where required means every file that has not declared itself illustrative. A report
that stayed green while its evidence went stale would convert "nobody has looked" into "somebody checked",
which is the failure this whole section exists to prevent. The scheduled `provider-drift` workflow runs the
same command, publishes the output to its job summary whether it passes or fails, and carries the same
verdict.

**Nothing automated moves a date.** A provenance block records that a person read a named source on a named
day. A job that edited the date would destroy the only fact the block carries. When something is stale, a
maintainer re-reads the source and edits the file by hand.

**No row is permanent.** A venue changes a field name, a fee rule or an error code whenever it likes. The
dated block is what makes that visible instead of silent.
