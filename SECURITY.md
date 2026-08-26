# Security policy

## What this repository is

Documentation skills for coding agents. The product is Markdown that an agent loads into its context. It
ships no runtime code that handles money, no credentials and no network client. What is executable lives
under `scripts/`, and it runs over this repository's own content, in CI or on a maintainer's machine. The one
exception is `scripts/install-guardrails.sh`, which a user runs against their own project and which is
described under [Risks this project has](#risks-this-project-has).

**This policy does not claim the repository is secure.** Every conclusion below names a check and the exact
commit at which it held. A statement that cannot be re-derived from a named check at a named commit is not a
security statement, it is a mood.

The largest risk here is not memory safety. It is a wrong financial claim that an agent acts on. A rule
stated confidently and falsely can produce a duplicate order, a double credit or an unrecoverable position,
and it does so quietly, in code that passes review. An incorrect invariant in this repository is treated as a
security defect, and it is reported in public.

## Reporting in public

### An incorrect financial invariant

Open a GitHub issue with the "Incorrect financial invariant" template. Include the primary source that
contradicts the rule. There is no embargo period and no private channel for this class: the rule is already
published, every reader is already acting on it, and a correction is worth more in the open than an
undisclosed fix is worth in private.

If the rule is wrong in a way that could cost someone money now, say so in the title. It is prioritised over
everything else in this repository.

### Provider drift

Every provider and protocol reference carries a provenance block: what was read, where, on which date, and
the trigger that should send someone back to re-read it. When a venue changes its API and a reference goes
stale, that is a defect, not a maintenance chore. Open a "Provider or documentation drift" issue with the
current source URL. The `provider-drift` workflow reports the age of that evidence weekly, and reports only:
it never edits a `verified_at` date, because the date records that a person read a source, not that a job ran.

## Reporting in private

Use GitHub private vulnerability reporting: the **Security** tab, then **Report a vulnerability**. It was
read as enabled on 2026-08-25 with
`gh api repos/nbailo/financial-engineering-skills/private-vulnerability-reporting`, which returned
`{"enabled":true}`. Repository settings are not versioned, so that is a dated observation rather than a
property of a commit. If the button is not there, open a normal issue saying only that you need a private
channel, and hold the detail until you have one.

Private is the right channel for:

- a way to make anything under `scripts/` execute attacker-controlled input, or write outside the directory
  it was pointed at;
- a way for a pull request, a fork or a scheduled job to reach a token, a runner or the code-scanning upload
  in this repository's workflows;
- a credential that has been committed here, whoever it belongs to.

Public is the right channel for a wrong rule, a stale provider fact or a dangerous example, as above.

There is no bounty, and no guaranteed response time. This is a small repository with one maintainer, and
promising an SLA that nobody is rostered to meet would be its own kind of false claim.

## What the automation is checked for

Each row below names the check that keeps it true, and each of those checks runs in CI on every push and
pull request. Re-derive any of them by running the named command against a checkout. Nothing here is pinned
to a commit: this repository has no release, and a property anchored to a commit is a claim about that
commit only.

| Property | Check that holds it |
| --- | --- |
| Every `uses:` in `.github/workflows/` names a full 40-character commit SHA, GitHub-owned actions included, with the release tag it was resolved from in a trailing comment | `workflow-hygiene` job, `python3 scripts/check_workflows.py` |
| Every workflow declares `permissions` at workflow level and every workflow-level scope is read-only | same check |
| The only write scope anywhere in the automation is `security-events: write`, on the single Scorecard job that uploads SARIF | same check, plus review of the one job |
| Every job declares `timeout-minutes` | same check |
| Every `actions/checkout` sets `persist-credentials: false`, so the job token is not left in `.git/config` for later steps | same check |
| Every `pip install` in a workflow passes `--require-hashes` | same check |
| Dependabot is monthly, grouped, capped at one open routine pull request, with pip routine updates off and pip security updates on | same check |

Supporting facts behind those rows, each with how it was obtained:

- **The pins.** Each tag was resolved to its commit SHA with `gh api repos/OWNER/REPO/git/ref/tags/<tag>` on
  2026-08-25, dereferencing annotated tags to the commit they point at, and two of them were confirmed a
  second time by cloning the repository at the tag and reading `git rev-parse HEAD`.
- **The platform backstop.** This repository also has GitHub's own "require actions pinned to a full-length
  commit SHA" setting turned on: `gh api repos/nbailo/financial-engineering-skills/actions/permissions`
  returned `"sha_pinning_required": true` on 2026-08-25. The same query returned
  `"default_workflow_permissions": "read"` and `"can_approve_pull_request_reviews": false`. These are
  repository settings, not commit content, so they are dated observations that a maintainer can change.
- **The one dependency.** `requirements.txt` pins PyYAML 6.0.3 by SHA-256. The digests were read from the
  PyPI JSON API on 2026-08-25 and re-checked by downloading each artifact and hashing it locally. CI installs
  it with `pip install --require-hashes -r requirements.txt`, so an artifact this repository has not pinned
  by digest cannot enter a job.
- **Shell.** The `shellcheck` job runs ShellCheck over every tracked `*.sh`, not over a list a new script can
  be added beside. It uses the copy in the GitHub hosted runner image and installs nothing, so a runner image
  that dropped ShellCheck would fail the job rather than skip the check and report green.
- **Dependency review.** Pull requests touching `requirements.txt` or `.github/workflows/**` run
  `actions/dependency-review-action` with `fail-on-severity: low`.
- **Secrets.** `.github/workflows/secret-scan.yml` scans the commit range the event adds, on every push and
  pull request, and exits non-zero rather than reporting green when the base commit is missing from the
  clone. The full-history scan is `scripts/secret-scan.sh` with no arguments, run by hand against a
  checkout; it covers every commit reachable from a local ref plus the tracked tree, for 13 literal-prefix
  patterns and 2 assignment patterns. Run it yourself rather than trusting a result quoted here.
  Separately, `gh api repos/nbailo/financial-engineering-skills` reported GitHub secret scanning and push
  protection enabled on 2026-08-25.
- **Standing supply-chain visibility.** `.github/workflows/scorecard.yml` runs OpenSSF Scorecard weekly,
  adapted from the official starter workflow with every action pinned, and uploads SARIF to code scanning.
  `publish_results` is left off and no badge is published: the findings are a description of how this
  repository is configured, read as findings. The number is not a release gate and not a quality claim, and
  a repository can score well while stating a false financial invariant, which is this project's actual risk.
  One limit of the pin is worth stating: pinning that action by commit SHA fixes the action definition, and
  the definition runs a container it names by tag, `docker://ghcr.io/ossf/scorecard-action:v2.4.4`, read in
  `action.yaml` at the pinned commit. The image itself is therefore not digest-pinned by this repository.

None of this says the repository is secure. Each row says that one named check runs and passes on the tree
you have checked out. A check that passes there says nothing about a later commit beyond the fact that the
check still runs on it.

## Risks this project has

### The installer

`scripts/install-guardrails.sh` writes a marked block into `AGENTS.md`, `CLAUDE.md` and
`.github/copilot-instructions.md` in the directory you point it at. It edits files in your repository, with
your permissions. Nothing in the documented path pipes network content into a shell: you clone the
repository, you check out and record a commit, you read the script, then you run it from that clone. The block is optional, and the skills work without it, and `--uninstall` removes it.

`scripts/test-install-guardrails.sh` is the evidence for what that script does and refuses to do, and CI runs
it on `ubuntu-latest` and `macos-latest`. It covers: a target that is a symlink, a non-regular file, or a file
with more than one hard link, and a `.github` that is a symlink, each refused before any write; markers that
are duplicated, unbalanced, reversed, embedded in a longer line, carrying a stray carriage return, or missing
their metadata line, each refused rather than guessed at; temporary files created only by `mktemp` inside the
destination directory, so a pre-created `AGENTS.md.tmp` symlink is never written through; existing
permissions and bytes outside the markers preserved; replacement by rename rather than in-place truncation;
a partial failure and an interrupt each restoring every file the run had already replaced and deleting every
file and directory it had created; uninstall never deleting a file that existed before install; and an
install followed by an uninstall leaving the host file byte-identical, including a file with no final newline
and a zero-byte file. Every tracked `*.sh` file also passes shellcheck in CI.

What that does not cover: the script runs with your user's permissions and can only be as safe as the tree
you ran it from, which is the reason the install path pins a tag and prints the commit before anything runs.

### CI

Workflows run on `push` and `pull_request`. No workflow uses `pull_request_target`, and no workflow reads a
secret: `grep -rn 'secrets\.' .github/workflows/` returns nothing at the commit above. A pull request from a
fork therefore runs with a read-only token and reaches nothing it could exfiltrate. The workflows execute on
GitHub hosted runners, on GitHub's platform, using actions written by others; pinning by digest bounds which
code runs, and bounds nothing else.

### Prompt content

These files are instructions that enter an agent's context and change what it does. Anyone who can change
this repository can change what a stranger's agent does, which makes review of a Markdown diff a security
control, not a style exercise. Two things follow. Install from a tag or a commit you have read, not from a
moving branch, and do not give an agent write access to its own skills directory.

At the commit above, no skill declares `allowed-tools`, so loading one grants no tool the agent did not
already have: `grep -rn 'allowed-tools' skills/ advanced/` returns nothing. Nothing enforces that today; it
is an observation, and it is worth re-running. The skills do contain shell and Python examples, including a
local fault-injection harness in `fin-verification`, and an agent that runs an example from a document runs
it with your permissions. Read before running.

### Dependencies and supply chain

One direct Python dependency, pinned by digest. A handful of actions, pinned by commit SHA. Dependabot is
configured to be quiet on purpose: at most one routine dependency pull request per month, grouped, with
security updates grouped per ecosystem and allowed at any time. There is no automerge, here or anywhere
else, because adopting a new commit of code that runs beside a token is a decision a person should make.
There is no Renovate configuration; a second bot with the same job would double the noise the limit exists to
control. There are no permanent `ignore` rules, and `scripts/check_workflows.py` fails if one appears, so a
temporary hold has to carry its reason and its expiry rather than becoming permanent by inattention.

### Distribution

Installs reach this repository through `npx skills add`, through the Claude plugin marketplace, or through a
clone. There are no releases, so the strongest thing you can pin is a commit SHA you have read. Install by
commit rather than by branch.

## Supported versions

There is currently no supported public release. `.claude-plugin/plugin.json` declares `0.0.0` rather than a
number that would imply a supported one. Development continues toward a 1.0.0 release.

Until then the supported thing is `main`. Corrections land there, and the way to get one is to move to a
newer commit. There are no maintenance branches and no backports. CI fails if a version stated in the README
or in `docs/` disagrees with the one in `plugin.json`.

## Scope and non-guarantees

Out of scope, and better reported elsewhere:

- The venues, processors, chains and payment rails these skills describe. Report those to their operators.
- Agent runtimes that load skills. Report those to the runtime vendor.
- The correctness of code an agent writes after reading a skill. The skills state properties and the tests
  that would prove them. They do not audit your implementation.

Non-guarantees, stated plainly:

- Following a skill does not guarantee that a system moves money correctly, and nothing here is a warranty
  against loss. The rules narrow a class of failure that repeats; they do not exhaust it.
- A provenance date records that a person read a named source on that day. It does not mean the source still
  says that today. That is the whole reason drift is reported weekly and reported honestly.
- The secret scan matches known credential shapes. It will not find a high-entropy string with no
  recognisable format, a bare hex exchange key, or a secret split across lines. A clean run is evidence about
  those patterns over that range, and nothing wider.
- Scorecard output describes configuration. It is not evidence that any financial rule in `skills/` is
  correct.
- CI checks the properties listed above. It does not review a financial claim, which is what human review and
  the cited primary sources are for.
