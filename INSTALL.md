# Installing

Pre-1.0: every path below installs the current `main` branch. Review updates before applying them to
sensitive financial code.

## The six skills

These use the `skills` CLI, version 1.5.23 at the time of writing.

```bash
npx skills add nbailo/financial-engineering-skills   # install the six
npx skills list                                      # verify what landed
npx skills update                                    # update
npx skills remove                                    # remove, chosen from the list
```

Add `-g` to install at user level instead of into the project. `npx skills add` discovers the six
skills under `skills/` and nothing under `advanced/`.

`npx skills remove` also takes names. Its `--all` flag is documented as every installed skill in
every agent directory, not only these six, so name what you mean.

## As a Claude Code plugin

Namespaces the skills so they cannot collide with anything else installed:

```
/plugin marketplace add nbailo/financial-engineering-skills
/plugin install financial-engineering-skills@financial-engineering-skills
```

`/plugin list` shows what is installed and
`/plugin uninstall financial-engineering-skills@financial-engineering-skills` removes it.

## The two advanced skills

Neither is installed by default. Install one deliberately. `--full-depth` is what makes the CLI look
below `skills/`, and `--skill` is what keeps the other one out:

```bash
npx skills add nbailo/financial-engineering-skills --full-depth --skill fin-matching-engine
npx skills add nbailo/financial-engineering-skills --full-depth --skill fin-market-data-publication
```

## Optional routing reinforcement

The skills are self-sufficient. What a skill cannot do is guarantee it gets consulted. If you want
routing to be more reliable, there is a small block you can install into the files every agent reads
on every turn: the routing table, and one instruction not to call a financial risk resolved just
because you described it.

**This one is a shell script that edits files in your repository.** Read it before you run it.

```bash
git clone https://github.com/nbailo/financial-engineering-skills fes
cd fes

# Print the digest of the one file that is about to execute, and read the script.
shasum -a 256 scripts/install-guardrails.sh   # or sha256sum

# Run its own test suite, which is what CI runs, then install where you want it.
./scripts/test-install-guardrails.sh
./scripts/install-guardrails.sh /path/to/your/repo
```

It writes a marked block into `AGENTS.md`, `CLAUDE.md` and `.github/copilot-instructions.md` in the
directory you point it at: this repository's `AGENTS.md`, plus a BEGIN marker, an END marker and one
metadata line. `scripts/install-guardrails.sh --uninstall .` removes it. The skills behave
identically either way.

Nothing in this path pipes a download into a shell. What the script does and refuses to do, and the
tests that hold it to that on both Linux and macOS, are in [SECURITY.md](SECURITY.md#the-installer).

## Verifying an install

```bash
npx skills list                                          # what landed
python3 scripts/validate.py                              # the repository's own checks
python3 examples/prediction-market-bot/run_tests.py      # the executable example, offline
```
