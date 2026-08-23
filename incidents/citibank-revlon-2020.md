# Citibank / Revlon — a payment system whose default was "send", suppressed only by setting three fields, confirmed by a dialog with no amount (2020-08-11)

**Domain:** Banking, syndicated-loan servicing, wire payments | **Loss:** ~$894 million wired; ~$385 million returned voluntarily; ~$500 million litigated | **Failure class:** Missing or overridable control (default direction and confirmation design) | **Skill:** fin-payments

## What happened

Citibank, as administrative agent on Revlon's 2016 $1.8bn term loan, intended to pay approximately
$7.8 million of accrued interest to lenders and to park approximately $894 million of principal in
an internal wash account as part of a roll-up. It wired the full $894 million principal to the
lenders. Three Citi personnel — a maker, a checker and an approver — reviewed the instruction and
all three passed it. Post-execution screenshots showing both figures were rendered and looked at.
The district court initially allowed the lenders to keep the money under the *Banque Worms*
discharge-for-value rule; the Second Circuit **vacated** that judgment on 8 September 2022 (49 F.4th
42) on inquiry-notice and not-presently-payable grounds. The popular account that "the court let
them keep it" is out of date.

## Root cause, in code terms

**The default direction of the money-moving control was "send".** Oracle Flexcube's behaviour, as
the Second Circuit describes it, is that the system "unless suppressed, causes any payment entered
into the system to be transferred as a wire payment". Doing nothing sends money out of the bank.
Suppression is the exceptional act.

**One semantic outcome required three independent fields, with no consistency check.** Citi's
internal Fund Sighting Manual stated that to suppress a principal payment, "**ALL of the below
field[s] must be set to the wash account: FRONT[;] FUND[; and] PRINCIPAL**". The maker set only
`PRINCIPAL`. Setting two of three, or one of three, produced a partially-suppressed instruction that
the system accepted and executed as a wire.

**Six-eye review multiplied one wrong mental model.** The maker, the checker and the approver each
independently "believed — incorrectly — that a wire transfer of the principal could be properly
suppressed by setting only the PRINCIPAL field". A review chain in which each reviewer confirms the
previous reviewer's screen does not test the shared assumption; it ratifies it three times.

**The confirmation dialog named neither the amount nor which amount.** A "stop sign" fired, reading:

> "Account used is Wire Account and Funds will be sent out of the bank. Do you want to continue?"

The Second Circuit records that it "indicated neither the amount that would be sent out of the bank,
nor whether it constituted the amount of the interest payment … the amount of the outstanding
principal, or a total of both." The checker clicked YES — correctly, from his point of view, because
he *did* intend to release the interest. A generic "are you sure" cannot discriminate between the
action the operator intends and the action the system is about to take.

**The evidence was rendered, reviewed, and not seen.** Post-execution Flexcube screenshots "showed
both $7.8 million in interest **and** $894 million in principal under the 'Amount Paid' field", and
the maker looked at them. Displaying a fact is not detecting a fact; only a comparison against an
independently derived expectation is detection.

## The invariant that was violated

```
# default direction
default_action(payment_control) == DO_NOT_SEND
send requires an explicit affirmative act; suppression must never require N affirmative acts

# one outcome, one control
suppress_wire is a single modelled choice
NOT: suppress_wire == (FRONT == wash) AND (FUND == wash) AND (PRINCIPAL == wash)
if multiple fields are unavoidable: assert consistency and BLOCK on mismatch

# confirmation content
confirmation.states(amount) AND confirmation.states(destination)
     AND confirmation.states(delta_from_expected)

# detection
detection := compare(actual_disbursed, independently_derived_expectation)
NOT: detection := a human reads a screen that contains the number
```

## Could an AI coding agent reviewing the diff have caught it?

**Partly — and the parts it can catch are precisely the parts that made a human error fatal.**

An agent cannot see an operator's mental model, and cannot know that three reviewers share one. It
can see, from the source of a payment form or handler:

- **A default branch that sends externally.** The signal is a code path where the outbound-wire
  behaviour is what happens when no suppression field is set — `if not suppressed: wire()` with
  `suppressed` defaulting to false. The correct shape inverts it: the destination is a required
  input, and "wire out of the bank" is a value the caller must supply.
- **Multiple independent fields expressing one semantic outcome, with no consistency check.** Three
  fields that must agree, validated nowhere, is a mechanical finding. The reviewing question — "what
  happens if `FRONT` and `FUND` disagree with `PRINCIPAL`?" — has no answer in the code, which is
  the defect.
- **A confirmation dialog string with no interpolated amount or destination.** A hardcoded prompt
  literal containing no format placeholders, gating a value transfer, is directly greppable.
- **A detection path that terminates in rendering rather than comparison.** A post-execution
  "verification" step that produces a screenshot or a screen and asserts nothing is not a control.

What no agent could catch is the human core of this incident. That is worth stating plainly: this
entry earns its place because it shows what code shapes turn an ordinary human error into a
$894 million wire, not because a reviewer would have prevented the error.

## The rule

> **MUST — Make the safe outcome the default for any money-moving control.** Require an explicit
> affirmative act to *send*; never require an explicit act to *suppress sending*.

> **MUST — Never require a user to set multiple independent fields to achieve one semantic outcome.**
> Model the outcome as a single choice. Where multiple fields are unavoidable, validate their
> consistency and block on mismatch.

> **MUST — A confirmation prompt for a money movement must state the exact amount, the destination,
> and the delta from the expected or previous value.** A generic "are you sure" is not a control.

> **SHOULD — Make an approver derive the expected outcome independently** — re-enter the amount, or
> confirm against a separately computed expectation — rather than confirming the maker's screen.
> Maker-checker does not defend against a shared incorrect mental model.

> **MUST — A verification step must terminate in a comparison against an independently derived
> expectation, not in a rendering.** Displaying a fact is not detecting it.

## Sources

- **`Citibank, N.A. v. Brigade Capital Management, LP` (In re Citibank August 11, 2020 Wire
  Transfers), 2d Cir. No. 21-487, 49 F.4th 42 (8 Sept 2022)** — opinion PDF:
  <https://www.consumerfinancialserviceslawmonitor.com/wp-content/uploads/sites/501/2022/09/Citibank-v.-Brigade-Capital-Management-et-al.-Revlon-Lenders-2d-Cir.-2022.pdf>.
  **Primary.** Pages 16–22 contain a verbatim account of the Flexcube default ("unless suppressed,
  causes any payment entered into the system to be transferred as a wire payment"), the Fund
  Sighting Manual's "ALL of the below field[s] must be set to the wash account: FRONT[;] FUND[; and]
  PRINCIPAL", the six-eye chain and the shared incorrect belief, the "stop sign" dialog text and
  the finding that it indicated neither the amount nor which amount, and the post-execution
  screenshots showing $7.8 million and $894 million together under "Amount Paid". This is the single
  best primary source on payment-UX authority failure in the corpus.
- **District court:** `In re Citibank August 11, 2020 Wire Transfers`, 520 F. Supp. 3d 390 (S.D.N.Y.
  16 Feb 2021) — the judgment the Second Circuit vacated.
- **Correction applied.** Secondary accounts almost universally state that the lenders kept the
  money. The Second Circuit vacated on 8 September 2022; any skill citing this incident must not
  assert that the funds were permanently lost.
