# Advanced: material outside the installed product

The six skills under `skills/` are the product. They cover code that trades through a venue, integrates a
payment processor, keeps a ledger, or moves value on-chain.

What lives here is for a much smaller audience: engineers whose code **is** the venue. Both skills below
are **BETA**: newer than the six, outside the default routing table, and the least exercised material in
this repository. Neither is installed by a default `npx skills add`, and neither consumes the shared
description budget, so neither costs anything until you ask for it by name.

Install one deliberately. `--full-depth` is what makes the CLI look below `skills/`, and `--skill` is what
keeps the other one out:

```bash
npx skills add nbailo/financial-engineering-skills#<commit> --full-depth --skill fin-matching-engine
npx skills add nbailo/financial-engineering-skills#<commit> --full-depth --skill fin-market-data-publication
```

`#<commit>` is a full commit SHA you have read. There is no release tag, and the CLI turns a `#` fragment on
a git source into the `ref` it clones, which a SHA satisfies. Drop the fragment and you install whatever is
on the default branch at that moment. The README at the repository root has the pinned clone, and the
verification steps that go with it.

From that clone, copying the directory works too, and the two commands below are the same thing by hand.

## fin-matching-engine

Code that owns an order book and mints the executions everyone else books: durable ordered input,
deterministic replay, enumerated order-state transitions, allocation conservation and residue, priority and
iceberg refresh, auction computation, self-match prevention, checked aggregates, fan-out bounds, pre-trade
risk controls, halt and resume, single-writer recovery.

```bash
cp -r advanced/fin-matching-engine ~/.claude/skills/     # from a clone of this repository
```

## fin-market-data-publication

Code that publishes a feed it originates: message and packet sequencing, snapshot and incremental joins,
resets and session identity, A/B arbitration, gap detection and recovery, conflation and backpressure, book
and volume filters, timestamp semantics, deterministic publication.

```bash
cp -r advanced/fin-market-data-publication ~/.claude/skills/     # from a clone
```

## Which one you want

If you are writing a trading bot, neither: you want `fin-exchange-integration`, which is about being a
venue's client, and that is the common case. The distinction that decides it is authority.
`fin-exchange-integration` is for code whose authority is EXTERNAL, where the venue holds the truth and you
reconcile against it. These two are for code whose authority is SELF, where nothing outside can tell you
that you are wrong, and replay and determinism are the only proof available.
