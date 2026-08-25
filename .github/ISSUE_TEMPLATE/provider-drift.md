---
name: Provider or documentation drift
about: A provider reference has gone stale against its source
labels: drift
---

**Reference file.** And its current `verified_at` date and `revalidate_when` trigger.

**What changed at the provider.** Endpoint, field, semantics, deprecation or version bump.

**Current primary source.** URL, and the SDK commit if code behaviour is involved.

**What the reference now gets wrong.** Quote the stale sentence.

**Is anything now dangerous rather than merely outdated?** A removed field that fails loudly is a nuisance;
a field whose meaning silently changed is a correctness problem.
