# Installing

There is no supported public release. Nothing here has been published as one, so the only thing
worth pinning is a commit you have read. Development continues toward a 1.0.0 release.

## The six skills

These use the `skills` CLI, version 1.5.23 at the time of writing.

```bash
npx skills add nbailo/financial-engineering-skills#<commit>  # install the six
npx skills list                                              # verify what landed
npx skills update                                            # update
npx skills remove                                            # remove, chosen from the list
```

`#<commit>` is a full commit SHA you have looked at. In the CLI's own source a `#` fragment on a
git source becomes the `ref` passed to `git clone --depth 1 --branch <ref>` (`src/source-parser.ts`,
`src/git.ts`, read at commit `435076e78988e1e6ec40d00b0b1d76bdbbc5419a`), and a SHA is a valid
`ref`. Without the fragment you install whatever is on the default branch at that moment, which is
not something you can review beforehand or reproduce afterwards.

The files land in your agent's own skills directory: `.claude/skills/` for Claude Code,
`.agents/skills/` for Codex, Cursor and several others. Add `-g` to install at user level instead
of into the project. `npx skills add` discovers the six skills under `skills/` and nothing under
`advanced/`.

`npx skills remove` also takes names. Its `--all` flag is documented as every installed skill in
every agent directory, not only these six, so name what you mean.

## As a Claude Code plugin

Namespaces the skills so they cannot collide with anything else installed:

```
/plugin marketplace add nbailo/financial-engineering-skills
/plugin install financial-engineering-skills@financial-engineering-skills
```

This tracks the default branch, and there is no tag to pin it to. Claude Code's marketplace
documentation states that a git-based marketplace source supports `ref`, a branch or tag, and not
`sha`, and that a commit SHA can be pinned only on a plugin source inside `marketplace.json`
(<https://code.claude.com/docs/en/plugin-marketplaces>). `/plugin list` shows what is installed and
`/plugin uninstall financial-engineering-skills@financial-engineering-skills` removes it.

## The two advanced skills

Neither is installed by default, and neither consumes the shared description budget. Install one
deliberately. `--full-depth` is what makes the CLI look below `skills/`, and `--skill` is what
keeps the other one out:

```bash
npx skills add nbailo/financial-engineering-skills#<commit> --full-depth --skill fin-matching-engine
npx skills add nbailo/financial-engineering-skills#<commit> --full-depth --skill fin-market-data-publication
```

## Optional routing reinforcement

The skills are self-sufficient. What a skill cannot do is guarantee it gets consulted. If you want
routing to be more reliable, there is a small block you can install into the files every agent
reads on every turn: the routing table, and one instruction not to call a financial risk resolved
just because you described it.

This one is a shell script that edits files in your repository, so install it from a commit you
have looked at.

```bash
# 1. Clone, then pin. There is no tag, so the pin is the commit you check out.
git clone https://github.com/nbailo/financial-engineering-skills fes
cd fes
git checkout <commit>   # a commit you have read

# 2. Verify before running any of it. The first prints the commit you are on, which is the
#    thing to record. The second prints the digest of the one file that is about to execute.
git rev-parse HEAD
shasum -a 256 scripts/install-guardrails.sh   # or sha256sum

# 3. Run its own test suite, which is what CI runs, then install where you want it.
./scripts/test-install-guardrails.sh
./scripts/install-guardrails.sh /path/to/your/repo
```

It writes a marked block into `AGENTS.md`, `CLAUDE.md` and `.github/copilot-instructions.md` in the
directory you point it at: this repository's `AGENTS.md`, which CI holds under 2,048 bytes, plus a
BEGIN marker, an END marker and one metadata line. `scripts/install-guardrails.sh --uninstall .`
removes it. The skills behave identically either way.

Nothing in this path pipes a download into a shell. What the script does and refuses to do, and the
tests that hold it to that on both Linux and macOS, are in [SECURITY.md](SECURITY.md#the-installer).

## Verifying an install

```bash
npx skills list                                          # what landed
python3 scripts/validate.py                              # the repository's own checks
python3 examples/prediction-market-bot/run_tests.py      # the executable example, offline
```
