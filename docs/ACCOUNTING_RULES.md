# Accounting rules

The rules this application implements, why each one is written the way it is,
and the decisions that are still open.

Written for: the operator of the household's accounts, and anyone maintaining
the code.

---

## 1. Money is integer paise

Every monetary value is an `int` number of paise, named `*_paise`, stored in an
`INTEGER` column. No floating-point arithmetic touches a financial value at any
point:

- the browser parses typed rupees to paise and refuses a third decimal place
  rather than rounding it away;
- the API rejects a float for a money field in request validation;
- the Python engines use integer and `Decimal` arithmetic only;
- `bool` is rejected explicitly, because `True` is an `int` in Python and
  accepting it would silently store 1 paisa.

Display is derived from the integer by splitting it into rupees and paise, so a
figure cannot drift on its way to the screen.

kWh readings are the one non-money quantity with a fraction. They are stored as
whole watt-hours, which gives exactly three decimal places of a kWh, and a
fourth is refused rather than rounded.

---

## 2. An expense is three separate facts

| Fact | Where it lives | What it means |
| --- | --- | --- |
| The household incurred a cost | `expense_instance.amount_paise` | Money left the household |
| Each resident owes a share | `allocation` rows | Who is liable, and for how much |
| Somebody funded it | `expense_instance.payer_resident_id`, **or** a linked `provider_advance` | Whose money actually went out |

These are never merged. One resident can pay a whole bill and still owe only
their own share of it: they are charged their share *and* credited the funding,
and the two figures are reported separately everywhere.

**Allocations must sum to the expense amount exactly.** This is enforced when
saving, re-checked by the validator, and asserted by the reconciliation, which
refuses to produce settlement figures if it is ever violated.

A resident who does not take part in an expense is given an **explicit zero**
in the allocation map rather than being left out, so an incomplete allocation
is always rejected instead of quietly accepted. Zero shares are reported in the
preview but not written as rows, because a zero-paise charge carries no
financial meaning.

---

## 3. Funding, and the double count it prevents

This is the rule the household is most exposed to getting wrong.

> **If any `provider_advance` payment is linked to an expense (`payment.expense_id`),
> the linked advances are that expense's funding record, and its
> `payer_resident_id` is ignored.**
>
> Only when no advance is linked does the payer field act as the funding record.

Without this, recording both — the expense with a payer, *and* the advance that
paid it — would credit the same resident twice for the same rupee.

Supporting rules:

- Linked advances **cannot exceed** the expense they fund. The API refuses it.
- If linked advances are **less** than the expense, the remainder is treated as
  funded by the pool, and the discrepancy is reported as a warning naming the
  ignored payer field.
- An **unlinked** advance that matches an expense in resident, amount, and date
  (within seven days) is flagged by name as a likely duplicate. It is a warning,
  not an error: the ledger still balances, and only the operator can know
  whether the two records describe the same payment.

---

## 4. Payments are not expenses

Four transaction types, none of which is a household expense:

| Kind | Effect on the payer | Effect on the pool |
| --- | --- | --- |
| `to_pool` | credited | debited |
| `provider_advance` | credited | debited |
| `refund` | debited | credited |
| `adjustment` *(legacy)* | debited | credited |

A `to_pool` payment is applied to the payer's **oldest open charges first**.
`provider_advance` and `refund` are not applied to personal charges: they fund
the household or return money from it.

A resident paying another resident directly is modelled as two of these — a
pool payment plus a refund — or as a pair of signed adjustments. Either way it
moves money between accounts; it never increases household expenses.

`adjustment` is retained from the prototype, where it meant "an extra amount due
from this resident". New corrections should use a signed `ledger_adjustment`
instead, which carries a reason and a reversal trail.

---

## 5. Oldest-first payment allocation

A pool payment settles the payer's charges in this order:

1. **charge due date** ascending — the due date defaults to the last day of the
   charge's settlement month;
2. **insertion sequence** ascending — the allocation row's `rowid`, which is
   monotonic;
3. **allocation ID** — a final tie-break that in practice is never reached.

The ordering is total, so the result never depends on a random identifier and
re-running the same inputs always produces the same applications.

**Targeted payments.** A pool payment may name one of the payer's own charges
that it is *for* — a resident paying their October rent. That charge is settled
first, up to what is still open on it; only the excess is then applied
oldest-first as above. This is how the Rent screen records a rent payment, so
October's rent shows as paid even when September still has something open. A
payment with no target is unaffected, and a target that is not the payer's own
charge is refused.

Anything beyond the payer's open charges becomes an **unapplied credit**. It
stays visible, carries forward across months, and is never consumed
automatically — applying it to later charges is an explicit operator action.

Per-charge and per-payment ceilings are enforced twice: in the service, and by
database triggers (`overapplied_payment`, `overapplied_charge`), so neither can
be breached even by direct SQL.

---

## 6. Accounting periods

Two periods, because conflating them is the other common source of error.

- **Service period** — the month the money was *for*.
- **Settlement period** — the statement month that *charges* it.

A definition's `period_rule` maps a transaction date to both:

| Rule | Service period | Settlement period |
| --- | --- | --- |
| `current_month` | month of the transaction date | month of the transaction date |
| `previous_month` | month before the transaction date | month of the transaction date |
| `transaction_date` | month of the transaction date | month of the transaction date |
| `custom` | operator-supplied | operator-supplied (**required**) |

`current_month` suits rent, broadband, maintenance, and furniture rental.
`previous_month` suits electricity and groceries: the September bill paid in
early October is charged in the **October** statement, while its service period
stays **September** so consumption reports remain truthful.

`transaction_date` behaves like `current_month` for period selection; it exists
to record that the date itself, rather than a month boundary, is the
authoritative marker for the charge.

Both periods can be overridden on any individual expense. `custom` *requires*
an explicit settlement period, so a rule can never silently guess.

A statement collects expenses whose **settlement period** equals its month —
never by transaction date. An expense cannot be attached to a statement for a
different month; the API refuses it with `settlement_period_mismatch`.

---

## 7. Residents and partial months: nothing prorates

**Decided 2026-10-11 by the household. This is a rule, not a gap.**

A resident's share does not change because they were only here for part of the
month. There is no pro-rata calculation anywhere in the application, for any
category.

- A resident who moves in on the 16th and takes part in an expense is charged a
  **full ordinary share** of it.
- A resident who moves out on the 10th and takes part is likewise charged a
  full share.
- `active_from` and `active_until` are recorded for reference — on the resident
  record, in reports, and in the audit trail — and they **never** alter an
  allocation.

What the operator still controls, expense by expense, is **who takes part at
all**. If somebody who arrived mid-month should not be in September's rent,
leave them out of the participants and they get an explicit zero. That is a
participation decision, not a proration one, and it is deliberate and visible
rather than calculated behind the scenes.

The practical consequence worth stating: because a fixed cost like rent is
always split in whole shares, the household collects the full amount every
time. There is never a shortfall for the other residents to absorb, which is
the problem proration creates and the reason this rule was chosen.

Locked in by `tests/test_invariants.py::test_nothing_prorates_for_a_partial_month`
and the tests beside it, so a future change cannot reintroduce proration
without a test failing.

---

## 8. Rounding

Indivisible remainder paise have to go somewhere, and the household agreed they
should not go to whoever paid.

**Equal split.** The base share (`amount // n`) is assigned to everyone first.
The remaining `amount % n` paise are distributed one each, to **non-payers
first in resident-ID order**, and only then to the payer. If the payer is the
only participant, they necessarily receive the remainder.

**Weighted and measured splits.** Largest-remainder method: each participant
gets the floor of their exact share, and the leftover paise go to the largest
fractional remainders. Ties break with the **payer placed last**, then by
resident ID.

The payer-preference rule is fully specified only for equal splits. The
weighted path applies the payer tie-break where remainders are equal, and the
UI warns that the preference is defined for equal splits — see
[open decisions](#15-decisions-still-open).

Every split totals the amount exactly, for any amount and any number of
participants. This is tested across the awkward cases.

---

## 9. Electricity

The **actual utility bill and its itemised charges are authoritative.** The
engine allocates the bill that arrived; it never reconstructs a tariff from
kWh and never estimates a total.

- Component amounts must sum to the bill total **exactly**.
- Each component's resident allocations must sum to that component **exactly**.
- The resident totals must sum to the bill total **exactly**.

All three are checked by the engine and re-checked before persistence.

Each component carries its own allocation rule, which is how exceptions are
configured:

- `usage` — in proportion to each resident's aggregate smart-plug kWh;
- `equal` — split equally;
- `custom` — exact paise per resident, or relative weights.

**Smart-plug readings are operator-supplied allocation weights**, not a
replacement for the bill.

**Guests are not billed residents.** A guest's kWh is added to the host's own
reading before the calculation runs, so the host carries the cost and no guest
account is ever created.

A positive usage-allocated charge with **no recorded usage is rejected**, not
estimated. A zero-amount charge allocates zero to everyone, which needs no
readings.

**A saved calculation is never silently changed.** Correcting one requires a
reason, writes the superseded calculation to the audit trail, is recorded as a
statement revision inside the correction window, and is refused outright once
the statement is closed — then the correction belongs in a ledger adjustment.

---

## 10. The statement lifecycle

```
draft  ──finalize──▶  finalized_correction_window  ──72h──▶  closed
                             │                                  │
                      numbered revisions                 ledger adjustments
```

**Draft.** Fully editable. Expenses are attached by settlement period,
recurring ones are generated, allocations can be recalculated.

**Finalized.** A snapshot is built and stored with a SHA-256 hash of its own
contents. A 72-hour correction window opens — the agreed operational reading of
"three days", configurable per statement. Corrections during it are allowed but
each requires a **reason** and is written as a numbered, immutable
`statement_revision`. Finalizing refuses by default while any error-severity
validation issue stands.

**Closed.** The snapshot is immutable. Enforced in the service layer *and* by
database triggers, so a direct SQL mistake cannot rewrite it either. Later
corrections are signed `ledger_adjustment` rows linked to the statement; the
statement itself is never rewritten.

The stored snapshot is re-readable byte for byte and its hash is verified on
every read. Statement exports are built from it when it exists, so an exported
finalized statement reproduces what was finalized rather than today's ledger.

Statements whose window elapsed while the application was not running are
closed at the next startup, recorded in the audit trail with actor `startup`.

---

## 11. Reversals and corrections

Nothing financial is edited in place or deleted once it has had an effect.

**Payments** are reversed, not edited: the original is kept and marked
`reversed`, a mirrored `reversal` row points back at it, and the charges it
settled reopen. Both rows stay in the ledger and both are excluded from net
positions, so the money nets to nothing while the trail stays complete. A
reason is required.

**Adjustments** are append-only, enforced by triggers. A mistaken adjustment is
corrected by a reversing adjustment that must be its **exact negative** and
that points at the original. An adjustment cannot be reversed twice.

**Expenses** can be deleted only while unattached or in a draft, and only if no
payment has been applied to their charges and no payment is linked as their
funding. The deletion itself is recorded in the audit trail.

**The audit log and statement revisions** cannot be updated or deleted at all.

---

## 12. Reconciliation

One calculation covering every recorded transaction across every month. It
turns records into signed **ledger entries** against named accounts — one per
resident, plus the synthetic `__pool__` account for the collection pool.

| Event | Entries |
| --- | --- |
| Expense incurred | each resident −share; pool +amount |
| Funded by a resident | funder +amount; pool −amount |
| Payment into the pool | payer +amount; pool −amount |
| Provider advance | payer +amount; pool −amount |
| Refund | recipient −amount; pool +amount |
| Signed adjustment | resident ±amount; pool ∓amount |

Positive means the account is owed money; negative means it owes money. Every
event writes equal and opposite entries, so the accounts sum to zero by
construction. The sum is asserted anyway — a non-zero result is a bug, and the
calculation refuses to report figures rather than paper over it.

**Two independent readings are cross-checked.** The net-position view (from the
entries) and the charge-level view (from allocations and payment applications)
must satisfy:

```
net = unapplied credit − outstanding charges + funding + adjustments
      − refunds − legacy payment adjustments
```

If they disagree, reconciliation raises `views_disagree` rather than reporting
either one.

An `as_of_date` restricts dated expenses, payments, and adjustments, which is
how a historical position is reproduced. Re-running with no new transactions
produces an identical result; all ordering and tie-breaks are total.

**Settlement suggestions never change records.** A greedy, deterministic
transfer plan matches debtors to creditors, and no suggested transfer ever
exceeds the balance it settles — asserted before the plan is returned.

---

## 13. When the data is ambiguous

The application never invents a balancing entry. Ambiguity is reported as a
named exception against a specific record.

**Error** — the ledger is internally inconsistent; settlement figures are
refused until it is fixed:

| Code | Meaning |
| --- | --- |
| `allocation_total_mismatch` | An expense's shares do not sum to its amount |
| `allocation_foreign_resident` | A charge belongs to somebody outside the apartment |
| `expense_not_allocated` | A non-zero expense has no shares |
| `payment_overapplied` | A payment is applied beyond its own amount |
| `charge_overapplied` | A charge is settled beyond its own amount |
| `advance_exceeds_expense` | Linked advances exceed the expense they fund |
| `reversed_payment_still_applied` | A reversed payment still settles charges |
| `statement_missing_snapshot` | A finalized statement has no stored snapshot |

**Warning** — the ledger reconciles, but a judgement call is hiding in it:

| Code | Meaning |
| --- | --- |
| `possible_double_counted_advance` | An unlinked advance looks like an expense's funding |
| `expense_partially_funded` | Linked advances cover only part of the expense |
| `statement_period_mismatch` | An expense sits in a statement for another month |
| `inactive_resident_open_balance` | A former resident still owes or is owed |
| `expense_missing_apartment` | A pre-upgrade expense is not linked to the apartment |

---

## 14. The accounting invariants

The twenty required invariants, plus one added when the household settled the
proration rule. Each is covered by at least one test in
`tests/test_invariants.py`, run against a realistic two-month household rather
than a single tidy expense.

| # | Invariant |
| --- | --- |
| 1 | All financial values are stored as integer paise |
| 2 | Every expense's allocations equal the expense total exactly |
| 3 | Electricity component totals equal the actual bill total |
| 4 | Electricity resident allocations equal the actual bill total |
| 5 | Payments cannot be allocated beyond the available payment amount |
| 6 | Oldest-first allocation is deterministic |
| 7 | Unapplied credits remain visible and are not lost between months |
| 8 | Provider advances are not double-counted |
| 9 | Transfers between residents do not create household expenses |
| 10 | A resident's share and the resident who funded it stay distinct |
| 11 | Household and resident balances reconcile to zero in aggregate |
| 12 | Settlement transfers do not exceed the balances they settle |
| 13 | Re-running a reconciliation without new transactions is identical |
| 14 | Re-running recurring generation does not duplicate expenses |
| 15 | Finalized statement snapshots remain reproducible |
| 16 | Closed statements cannot be silently modified |
| 17 | Adjustments are traceable and affect balances exactly once |
| 18 | Date cutoffs and accounting-period rules behave consistently |
| 19 | Invalid data cannot partially modify the database |
| 20 | Rounding behaviour is deterministic and documented |
| 21 | Nothing prorates: a partial month never reduces a share |

---

## 15. Decisions still open

These need a human decision before they can be implemented. Each is listed with
what the application does in the meantime.

### 15.1 Move-in and move-out proration — **decided, no longer open**

**Decided 2026-10-11: nothing prorates.** A partial month does not reduce
anybody's share. See [§7](#7-residents-and-partial-months-nothing-prorates) for
the rule and what the operator still controls.

### 15.2 The payer rounding preference beyond equal splits

**Partially open.** The agreed rule — the payer does not receive a remainder
paisa — is fully specified for equal splits. For weighted and measured splits,
the payer is placed last in the tie-break when fractional remainders are equal,
but a payer with a genuinely larger remainder still receives the paisa.

**Current behaviour.** Implemented as described, and the allocation preview
warns about it on the weighted path.

### 15.3 "Three days" as 72 hours

**Proposed, implemented, confirmable.** The correction window is 72 hours from
the moment of finalization. The alternative reading — three calendar days
ending at a configured local time — is not implemented.

**Current behaviour.** 72 hours, configurable per statement, so a household
that prefers a different length can set one without a code change.

### 15.4 Oldest-first: due date or charge date

**Resolved, worth confirming.** Ordering is by charge **due date**, then
insertion sequence. The due date defaults to the last day of the settlement
month, so charges settled in the same month share one and fall back to creation
order.

### 15.5 What happens to an overpayment

**Resolved, worth confirming.** An overpayment stays an unapplied credit and
carries forward. It is never applied to future charges automatically; applying
it is an explicit operator action.

### 15.6 Minimum-transfer settlement

**Open.** The transfer planner is greedy and deterministic. It produces few
transfers and never one that exceeds the balance it settles, but it does not
promise the globally minimal count for every possible input.

### 15.7 Partial payment reversal

**Open.** `reverse_payment` reverses a payment in full. Reversing part of one
is not supported; record the full reversal and a corrected payment, or use a
signed adjustment.

### 15.8 Grocery purchase settlement state

**Partially open.** The specification describes grocery purchases being settled
promptly and not re-charged at month end. The application models this through
the ordinary charge/payment mechanism — a grocery expense creates charges,
which a payment settles — rather than a separate "settled purchase" flag. If
the household wants purchases marked settled independently of payment
applications, that rule needs defining.
