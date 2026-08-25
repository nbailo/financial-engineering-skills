# Security policy

## What this project is, and what it is not

These are documentation skills for coding agents. They ship no runtime code that handles money, no
credentials, and no network client. The one executable is `scripts/validate.py`, which reads files in this
repository and uses the Python standard library plus PyYAML.

The realistic risk here is not a memory-safety bug. It is **a wrong financial claim that an agent acts on**.
A rule that is confidently stated and false can produce a duplicate order, a double credit or an unrecoverable
position, and it will do so silently. Treat an incorrect invariant as a security-relevant defect.

## Reporting an incorrect financial invariant

Open a GitHub issue using the "Incorrect financial invariant" template. Include the primary source that
contradicts the rule. There is no embargo period and no private channel: the rules are public, the sources are
public, and a correction is more useful in the open.

If a rule is wrong in a way that could cause immediate loss to someone following it, say so in the title and
it will be prioritised over everything else.

## Reporting provider drift

Provider references carry a `verified_at` date and a `revalidate_when` trigger. When a venue changes its API
and a reference goes stale, that is a defect, not a maintenance chore: open a "Provider or documentation
drift" issue with the current source URL.

## Reporting a vulnerability in the validator

`scripts/validate.py` runs in CI over repository content. If you find a way to make it execute untrusted
input or escape the repository, open an issue and mark it clearly.

## What is out of scope

- The venues, processors and chains described here. Report those to their operators.
- Agent runtimes that load these skills. Report those to the runtime vendor.
- The correctness of code an agent writes after reading a skill. The skills state properties and the tests
  that prove them; they do not audit your implementation.
