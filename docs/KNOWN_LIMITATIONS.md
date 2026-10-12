# Known limitations

What this application does not do, stated plainly, so nothing comes as a
surprise later.

---

## Business rules that are deliberately not implemented

### Move-in and move-out proration

**Not a limitation — a decision.** The household decided on 2026-10-11 that
**nothing prorates**: a partial month does not reduce anybody's share, for any
category. A resident who takes part in an expense is charged a full ordinary
share of it, whether they were here for thirty days or three.

`active_from` and `active_until` are recorded for reference and never alter an
allocation. What the operator still controls, expense by expense, is who takes
part at all — leaving somebody out gives them an explicit zero, which is a
participation decision rather than a proration one.

Documented in [`docs/ACCOUNTING_RULES.md` §7](ACCOUNTING_RULES.md#7-residents-and-partial-months-nothing-prorates)
and locked in by tests, so it cannot drift back.

### The payer rounding preference on weighted and measured splits

For an **equal** split, the rule is fully implemented: remainder paise go to
non-payers first, so the payer is never charged the spare paisa.

For **weighted** and **measured** splits, the payer is placed last only in the
tie-break — when two residents have the *same* fractional remainder. A payer
with a genuinely larger remainder still receives the paisa, because doing
otherwise would break the largest-remainder method's proportionality.

The allocation preview warns about this on the weighted path.

### Minimum-transfer settlement

The suggested-transfer planner is greedy and deterministic. It produces few
transfers and never one that exceeds the balance it settles, but it does not
promise the globally minimal number of transfers for every possible set of
balances. For a household of a handful of people the difference is rarely more
than one transfer.

### Grocery "settled purchase" state

The specification describes grocery purchases being settled promptly and not
re-charged at month end. This is modelled through the ordinary mechanism — a
grocery expense creates charges, and a payment settles them — rather than a
separate flag on the purchase. If the household wants purchases marked settled
independently of payment applications, that rule needs defining first.

---

## Functional gaps

### Partial payment reversal

`reverse_payment` reverses a payment **in full**. To correct part of one,
reverse it and record a corrected payment, or use a signed adjustment.

### Rent: one lease, split equally

Rent management covers one arrangement: a single master lease with one monthly
rent, divided equally between the residents taking part. Unequal room rents,
several leases, and rent that changes part-way through a month are not
modelled. The lease form lists four other arrangements as "Under Development";
they are placeholders and cannot be selected. A month's rent can still be
recorded by hand as an ordinary expense with weighted or custom shares; the
Rent screen will not adopt it, because it only manages equal splits.

### The lease term is tracked, not enforced

The contract term, notice window and lock-in are information for the household.
Nothing is blocked when a term expires or a lock-in is broken; rent keeps
generating at the current rate until the lease is renewed.

The security deposit is recorded as a figure on the lease only. It is not a
ledger entry, and neither is its refund: the deposit recovery shown when
serving notice is an on-screen estimate, and the deductions entered there are
kept only as a note in the audit trail. There is no step that marks a lease as
finally vacated.

### Rent already generated at another amount is adopted as it is

If a month's rent already exists as an expense — for example from the
recurring generator, at the Rent category's default amount — generating rent
for that month links that expense at its own amount rather than charging the
lease rent. Keep the Rent category's default amount in step with the lease, or
generate rent from the Rent screen first.

### Removing a resident during setup marks them moved out

The guided setup can rename a saved resident and remove one, but removal is the
same as moving out: the record is kept as inactive. A name added by mistake
cannot be deleted outright.

### A recorded landlord payout cannot be undone

Recording a payout freezes the month's rent cycle and issues the HRA receipts,
which are immutable. There is no "undo payout". The advance that funded a
resident-paid payout cannot be reversed either. A payout recorded against the
wrong resident is corrected with a pair of signed ledger adjustments, which
leaves the mistake and its correction both visible.

### HRA receipts are household records, not the landlord's

The HRA slip is generated from the household's own records once the payout is
recorded. It states each resident's share of the rent that reached the
landlord, and says on its face that the landlord did not issue it. It is issued
whether or not the resident has yet paid their share into the pool; the screen
says so when they have not. Whether an employer accepts it is their decision.

### Editing an electricity bill's allocations in place

An electricity bill is corrected by **recalculating** it — supplying the
corrected readings or charges — not by editing individual allocations. The
superseded calculation is written to the audit trail first. Recalculation is
refused if any payment has already been applied to the bill's charges; reverse
those first.

### Editing an expense that has been partly paid

An expense whose charges have payments applied cannot have its allocations
replaced. Reverse the payment first, or record a ledger adjustment. This keeps
a payment from silently pointing at a charge that no longer means what it did.

### Expenses with multiple components in the general editor

The general expense editor handles single-component expenses. An expense with
itemised components (currently only electricity) is edited through its own
module. The editor says so rather than failing partway.

### No PDF generation

Statements print from the browser (**Print** on the statement screen) and
export as CSV and JSON. There is no server-side PDF generator. The printed
view and the exports come from the same stored snapshot, so they agree.

### No charts

Figures are presented as tables with explicit totals. Nothing on any screen is
a decorative visual, and there are no placeholder charts.

### One operator, no permission system

There is no login, no user accounts, and no roles. The audit trail records an
actor name taken from `AEM_OPERATOR_NAME`, which is a label, not an identity.
This matches the stated scope: a single operator on their own machine.

### One currency

Amounts are integer minor units and display as INR with Indian digit grouping.
The `currency_code` column exists but nothing converts between currencies or
formats another one.

---

## Technical limitations

### SQLite concurrency

One writer at a time, with a 15-second busy timeout. Entirely adequate for one
operator; it is not a multi-user server.

### No automatic background jobs

Nothing runs on a timer. Statements whose correction window elapsed are closed
at the next startup, or when you press the button, or via
`scripts/manage.py close-expired`. A household that wants it hands-off can put
that command in cron.

### Reconciliation recomputes from scratch

Every reconciliation reads the whole ledger. That is what makes it auditable
and reproducible, and at household scale — thousands of records — it is a few
milliseconds. It would need incremental caching at a scale this application is
not for.

### Snapshot reproducibility has a defined meaning

A finalized snapshot is **stored**, hashed, and re-readable byte for byte; the
hash is verified on every read, and exports are built from it. That is the
guarantee.

What is *not* guaranteed is that recomputing the figures today would produce
the same result, because a back-dated record added afterwards would legitimately
change what the live ledger says about that month. That is why the snapshot is
stored rather than recomputed, and why post-close corrections are adjustments
rather than edits.

### The frontend bundle is not code-split

One ~370 KB JavaScript bundle (about 100 KB gzipped). Loaded from localhost,
splitting it would buy nothing measurable.

### No frontend end-to-end browser tests

The frontend has unit and interaction tests (68, with React Testing Library)
covering money formatting, the display guarantees, and the critical forms. The
complete user journey is tested end to end **through the API** in
`tests/test_end_to_end.py` rather than through a browser driver. The rendered
UI was verified manually against a running server.

---

## Data notes

### Legacy columns are kept

`expense_instance.expense_period` and `expense_instance.is_settled` are
superseded by `settlement_period`/`service_period` and by derived settlement
respectively. They are retained so older data and the original callers keep
reading correctly, and they are written on new rows for the same reason. They
are documented as legacy in the data model.

### The legacy `adjustment` payment kind

`payment.payment_kind = 'adjustment'` is retained from the prototype, where it
meant "an extra amount due from this resident". Reconciliation still honours it
so historical data reconciles, but new corrections should use a signed
`ledger_adjustment`, which carries a reason and a reversal trail.

---

## What is covered, for contrast

So the list above is read in proportion — all of the following are implemented
and tested:

- the twenty required accounting invariants, against a realistic two-month
  household with partial payments, an overpayment, a linked provider advance,
  an electricity bill, a closed statement, and a post-close adjustment;
- migration from a populated prototype database with counts, totals, and
  reconciliation all verified;
- the complete operator journey over HTTP, including a restart;
- the API contract, the error envelope, the confirmation guards, and every
  report and export;
- integer-paise storage end to end, enforced in the browser, in request
  validation, in the services, and by the column types.
