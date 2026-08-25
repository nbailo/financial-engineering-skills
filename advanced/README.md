# Advanced: material outside the installed product

The six skills under `skills/` are the product. They cover code that trades through a venue, integrates a
payment processor, keeps a ledger, or moves value on-chain.

What lives here is for a much smaller audience: engineers whose code **is** the venue.

## fin-matching-and-settlement

Matching against resting orders, pro-rata allocation, auctions, self-trade prevention, price bands and
halts, market-data publication, netting, settlement and liquidation.

It is not installed by default, and it is not part of the default routing, because including it would make
the package look like a toolkit for building institutional trading venues. It is not one. If you are
writing a trading bot, you want `fin-exchange-integration` instead: that skill is about being a venue's
client, which is the common case.

Install it deliberately:

```bash
cp -r advanced/fin-matching-and-settlement ~/.claude/skills/
```

The distinction that decides which you need: `fin-exchange-integration` is for code whose authority is
EXTERNAL, where the venue holds the truth and you reconcile against it. This skill is for code whose
authority is SELF, where nothing outside can tell you that you are wrong, and replay and determinism are
the only proof available.
