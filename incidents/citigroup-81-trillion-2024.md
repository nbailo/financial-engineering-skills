# Citigroup — $81 trillion credited where $280 was intended, with no plausibility ceiling anywhere on the path (2024-04, disclosed 2025-02)

**Domain:** Banking, payment entry and approval | **Loss:** $0 realised — reversed within hours, disclosed to the Federal Reserve and the OCC as a "near miss" | **Failure class:** Sentinel escape and missing control | **Skill:** fin-payments

## What happened

In April 2024 a customer account was credited with **$81 trillion** where the intended amount was
**$280**. A payments employee entered it and a second reviewer approved it; it was
missed by both and caught roughly 90 minutes after it was posted, and reversed several hours later.
(That it cleared to process the following day, and that a *third* employee was the one who caught
it, are FT details not carried by the other outlets.) No funds left the bank. Citi reported it to the Federal Reserve and the OCC
as a near miss, and disclosed that it had **10 near misses of $1bn or more in 2024 and 13 in 2023**.
The incident became public through Financial Times reporting on 28 February 2025.

**Sourcing warning, stated up front.** There is no regulator order, enforcement action, or bank
postmortem describing this event. Everything below is press reporting of Citi's own internal and
supervisory disclosures. The *outcome* (amount, date, review chain, reversal, near-miss counts) is
consistently reported across the FT, Reuters, CNBC and Bloomberg. The *mechanism* rests on a single
strand of FT reporting and is flagged as such where it appears.

## Root cause, in code terms

**The established part: there was no upper bound on the amount, anywhere on the entry-and-approval
path.** Two humans passed a number that no product, no account, and no counterparty could plausibly
justify. Detection came from a third human, after processing, by reading. Whatever the input error
was, the system had no opinion about magnitude at all — the ceiling on a credit was the range of the
data type.

**The reported mechanism, attributed:** FT reporting describes a rarely-used backup interface in
which the amount field was **pre-populated with fifteen zeros** that the operator failed to clear.
If accurate, this is a second, distinct defect on top of the missing bound: a field pre-filled with
a value that is *valid if submitted unchanged*. A default that is submittable is not a default; it
is a proposal the system is willing to execute. This detail is not corroborated by the other
outlets covering the story and should be attributed to the FT, not asserted as established.

**On the "how big is $81 trillion" comparisons.** The two research briefs behind this catalogue give
conflicting multiples — one says roughly 4× US GDP, the other roughly 800× world GDP. Against 2024
figures (US GDP ≈ $29tn; world GDP ≈ $105tn) neither is right. This entry asserts no multiple. The
point does not need one: **no business ceiling derived from any product, account or operator permits
a credit of $81 trillion**, and that is the entire finding.

**The base rate is the second lesson.** Ten billion-dollar near misses in 2024 and thirteen in 2023,
self-reported by one bank, means an institution of that size generates a billion-dollar error
roughly monthly and catches them by manual review. Review capacity is being used as a control, and
review capacity does not scale with the magnitude of the number.

## The invariant that was violated

```
# at the point of entry, before any approval step
0 < amount <= ceiling(product, account, operator)
where ceiling is finite, configured, and business-derived
   (e.g. <= N x the largest legitimate transaction for this product in the last 12 months)
and the response to a breach is REJECT, not WARN

# defaults
forall amount_field f: f.default is either empty or NOT submittable
NOT: f.default is a numeric literal that is valid on submit

# detection
detection_control is automated and fires before processing
NOT: detection_control is "a human eventually reads the number"
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes. This is the most mechanical check in the entire catalogue.**

Reviewing a payment-entry endpoint, a ledger-posting handler, or an admin credit form, the finding
is the absence of an upper bound on `amount`. It is a static, decidable property of the code: the
validation for `amount` checks type, checks positivity if you are lucky, and does not check
magnitude against any configured ceiling. An agent can flag this without knowing anything about the
business — the *presence* of a bound is what it checks; the value belongs to configuration.

The second finding is equally static: an amount field initialised to a numeric literal in the form
model or the request default. `amount = 0`, `amount = "000000000000000"`, `defaultValue={0}` on a
money input — a default that would be accepted if submitted unchanged.

The third is architectural and still visible: an approval workflow in which the only mechanism that
can reject an out-of-range value is a human reading a screen. If no code path can reject on
magnitude, then approval is not a control over magnitude, and an agent can say so.

## The rule

> **MUST — Enforce a hard, configured maximum on every operator- or API-supplied amount at the point
> of entry, sized to the business, and reject — not warn — above it.** The bound must be finite and
> derived from something real (product limit, account history, counterparty exposure), never from
> the range of the data type.

> **MUST — Never pre-populate an amount field with a value that is valid if submitted unchanged.**

> **MUST — Magnitude anomaly detection on a money path must be automated and must fire before
> processing.** Human review is not a bound; it is a sample.

## Sources

- **Financial Times, 28 Feb 2025** — <https://www.ft.com/content/9921925e-5a32-48cc-a3e3-3f77042477d2>
  (paywalled). **Secondary, and the origin of every other account.** Sole source for the reported
  mechanism (a rarely-used interface with a pre-populated field of fifteen zeros).
- **Reuters, 28 Feb 2025** —
  <https://www.reuters.com/business/finance/citigroup-mistakenly-credits-customer-account-with-81-trillion-near-miss-ft-2025-02-28/>.
  **Secondary.** Corroborates the amount, the intended $280, the review chain and the reversal.
- **CNBC, 1 Mar 2025** —
  <https://www.cnbc.com/2025/03/01/citigroup-mistakenly-credited-a-customer-account-with-81-trillion.html>.
  **Secondary.** Corroborates verbatim: "$81 trillion … when it meant to send just $280"; "took
  place last April"; "missed by two employees but caught 90 minutes after it was posted"; "reversed
  several hours later and reported to the Federal Reserve and Office of the Comptroller of the
  Currency as a 'near miss'"; and the base rate — "10 near misses of $1 billion or more last year
  and 13 in the year prior". CNBC does **not** say a *third* employee caught it, does **not** say
  the entry cleared to process the following day, and does **not** mention a pre-populated field;
  those three details are FT-only and are attributed as such above.
- **No primary source exists.** No regulator order, no enforcement action, no bank postmortem
  describes this incident. The rule it motivates does not depend on the mechanism detail — the
  absence of a plausibility ceiling is established by the outcome alone.
