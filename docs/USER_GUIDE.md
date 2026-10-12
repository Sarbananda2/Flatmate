# User guide

How to run a month, start to finish.

Written for: the person who keeps the household's accounts.

---

## Before you start

Open **http://127.0.0.1:8787** after running `./scripts/run.sh`.

The **month selector** in the header is the settlement month every screen works
in. Change it there once and every page follows; it is remembered between
visits.

The topbar ribbon provides four master destinations:
- **Month Hub** — Settlement lifecycle, cumulative balances, peer-to-peer settlement transfers.
- **Housing & Utilities** (`/services`) — Recurring apartment contracts & meters: Rent, Electricity, Water, Gas, WiFi, Domestic Staff.
- **Activity Ledger** — Unified ledger: ad-hoc expenses, pool payments, provider advances, and audit log.
- **Statements** — Monthly draft assembly, 72-hour correction window, cryptographic snapshots, and exports.

Three words are used precisely throughout, and the application never mixes them
up:

| Term | Means |
| --- | --- |
| **Household expense** | Money the household spent. It is split into resident charges. |
| **Charge** | One resident's share of a household expense. What they owe. |
| **Payment** | Money moving between a resident, the pool, or a provider. **Never an expense.** |

---

## First-run setup

### The quick way: the guided setup

Open the app on a new database and it takes you there automatically. Four
steps:

1. **What is this place called?** Just the name. The short identifier used in
   exports is worked out from it.
2. **Who lives here?** Everyone at once. Leave spare boxes empty.
3. **What does the household pay for?** The usual essentials are pre-ticked,
   each with the right accounting month already chosen. Untick what you do not
   pay, type amounts if you know them (optional), and add anything of your own.
4. **Ready.** A summary, and an offer to open this month's statement so you can
   start recording straight away.

**If you close it by accident, nothing is lost.** Each step saves when you
press Continue, and reopening the app brings you back to exactly where you
stopped — it works out where that is from what has actually been created, so
it is still correct after clearing your browser or moving to another machine.
Navigate away mid-setup and a banner at the top offers to pick it up again.

You can reopen the guided setup at any time from **Setup & health**, and
nothing it creates is locked: every resident and category stays editable.

### The manual way

If you would rather do it piece by piece, or you are adding to an existing
household:

### 1. Create the apartment

**Setup & health → New apartment.** Give it a short ID (`home` is fine) and a
name. The ID appears in exports and cannot be changed later.

### 2. Add the residents

**Residents → Add resident.** One per person sharing the apartment. Record the
move-in date if you know it.

Nobody is ever deleted here. When somebody moves out you record that, and all
their history stays.

### 3. Create the expense categories

**Expense categories.** Nothing is built in — rent, electricity, groceries and
the rest are records you create. Either press **Add the starter set** on the
Setup screen for a conventional seven, or create your own.

For each one you choose:

**The period rule** — this decides which statement month charges an expense
entered against it, and it is the setting worth getting right:

| Rule | Use it for | What happens |
| --- | --- | --- |
| **Current month** | Rent, broadband, maintenance, furniture rental | October's rent is charged in the October statement |
| **Previous month** | Electricity, groceries | The September bill you pay in early October is charged in the **October** statement, and still counts as **September** consumption |
| **Transaction date** | One-off purchases, repairs | Charged in the month it happened |
| **Custom period** | Anything the above misstate | You pick both months yourself |

**The default split** — equal, weighted, custom, or measured usage (for
electricity).

**Recurrence and a default amount** — if it recurs monthly, give it a typical
amount. That is only a suggestion: you confirm the real amount each month.

---

## Running a month

### Step 1 — Create the draft statement

**Statements → Create <month> draft.** One statement per settlement month.

### Step 2 — Generate the recurring expenses

**Expense categories → Recurring expenses for <month>.** You see every
recurring category, the amount it suggests, which months it covers, and
whether it has already been recorded.

Adjust any amount, tick the ones you want, and press **Generate**. You confirm
before anything is created.

Pressing it twice is safe. A category that already has an expense for the month
is shown as *Already recorded* and skipped — the application will not
double-charge the household.

### Step 3 — Record the other expenses

**Expenses → Record an expense.**

Fill in the description, amount, and transaction date. Picking a category
adopts its period rule and defaults. Check the two period fields: the **service
period** is what the money was for, the **settlement month** is the statement
that charges it.

**Who funded it.** If one resident paid, choose them under *Paid by*. They are
credited the full amount for funding it, and still charged only their own
share — those are different figures and the form says so. Leave it empty if the
money came from the household pool.

**How it is split.** Choose equal, weighted, or custom. The **allocation
breakdown** appears as you type, calculated by the same engine that will save
it, so what you see is what gets written. A resident who does not take part
shows an explicit zero rather than being left out. The form will not submit
until the shares total the expense exactly.

### Step 3b — Manage the master lease rent

**Rent (`/rent`).** Manages the single master lease and its equal split.

1. **Lease Settings.** Enter the landlord's name, PAN, bank account/UPI, total monthly rent, due day (1-28), and whether you receive formal landlord receipts.
2. **Generate / Adopt Rent.** On the 1st of the month, generate the rent cycle. It splits the rent equally across active participants ($1/N$). If rent was already recorded through recurring expenses, it is **linked** rather than duplicated.
3. **Targeted Rent Collection.** When a resident pays their rent, click **Record Payment** directly in their row on the Rent screen. Unlike general payments which apply oldest-first across all household expenses, this targeted payment settles their **rent allocation first**.
4. **Record Landlord Payout.** When the rent is transferred to the owner, record the payout (funded either by the apartment pool or by a resident advance).
5. **Landlord Receipts & HRA Slips.**
   - If the lease expects a landlord receipt, it can be attached during payout or deferred and uploaded later.
   - Click **View HRA Slip** for any participant to download their formal Section 10(13A) IT Act rent receipt.

### Step 4 — Enter the electricity bill

**Electricity → Enter a bill.** This is a two-step flow on purpose.

Enter the bill **exactly as the provider issued it** — the total is
authoritative, and the application never estimates it from kWh.

Enter each **itemised charge** separately and give each its own rule. Typically
energy charges follow measured usage while fixed and common-area charges split
equally. A charge that only one resident should bear is a *custom* charge with
their amount and zeros for everyone else. The charges must add up to the bill
total exactly.

Enter each resident's **smart-plug reading** in kWh, to three decimal places.

> **Guests.** Add a guest's usage to the **host's** reading. Guests are never
> billed directly; the host carries the cost.

Press **Review the calculation**. You see each resident's kWh, their share of
usage, their amount, and the per-charge breakdown, with a clear statement of
whether everything reconciles to the bill. Nothing is saved until you press
**Commit this calculation**.

If a resident paid the bill directly, record that next (step 5) and **link it
to the bill**.

### Step 5 — Record payments

**Payments & credits.** Four different things, and the screen keeps them apart:

**Into the pool** — a resident handed money to the household. It settles their
oldest open charges first, and the receipt tells you exactly which charges it
settled. Anything left over stays a visible credit.

**Paid a provider** — a resident paid an external bill for the household.

> **Always link it to the expense it funds.** Choose the expense in the form.
> Linking means that expense's *Paid by* field is deliberately ignored, so the
> resident is credited **once**, not twice. If you do not link it, the
> Reconciliation screen flags it by name as a likely double count.

**Refund** — the pool paid money back out to a resident.

**Adjustment** — a signed correction against one resident. This is the only way
to correct a statement that has already closed.

> **One resident paying another directly** is not an expense. Record it as a
> payment into the pool from the one who paid, plus a refund to the one who
> received it.

### Step 6 — Review

**Statements → open the month.** You see:

- every expense, with its periods, how it was funded, and each resident's share;
- any expense that settles in this month but is **not yet attached** — press
  *Attach this month's expenses* if it belongs here;
- the full cross-month reconciliation: outstanding charges carried forward,
  credits, funding, and adjustments;
- suggested settlement transfers;
- anything that needs attention before finalizing.

Errors in red must be fixed before you can finalize. Warnings in amber are
judgement calls: read them, and finalize if they are correct.

### Step 7 — Finalize

Press **Finalize…**. The dialog shows what you are about to commit, warns if
any of this month's expenses are still unattached, and requires an explicit
confirmation.

Finalizing is **not reversible**. It stores a hashed snapshot and starts a
**72-hour correction window**.

### Step 8 — Corrections inside the window

For three days you can still correct the data. Editing an expense asks for a
**reason**, and the change is recorded as a numbered, permanent revision. The
revision history is shown on the statement.

### Step 9 — Close

When the window has elapsed, press **Close…**. (It also closes automatically
the next time the application starts.)

After closing, the snapshot is immutable — in the application and in the
database itself. Later corrections become ledger adjustments.

---

## After a statement closes

### Correcting a closed month

**Payments & credits → Adjustments → Record an adjustment.**

Choose the resident, the direction, the amount, and a reason. Link it to the
closed statement to document which month it corrects. The original statement is
untouched; the adjustment is its own event, and it affects balances exactly
once.

An adjustment entered in error is corrected by **reversing** it — which writes
its exact negative — never by editing it.

### Reversing a payment

**Payments & credits → Reverse.** A reason is required. The original is kept
and marked reversed, a mirrored reversal entry points back at it, and the
charges it settled reopen. Nothing is deleted.

### Applying a credit

A credit sits visibly under **Unapplied credits** until you decide to use it.
Press **Apply to open charges** to put it against that resident's oldest open
charges. It is never applied automatically.

---

## Reading the numbers

### Balances

| What you see | What it means |
| --- | --- |
| **+₹500.00** (green, with a plus) | The household owes this resident |
| **−₹500.00** (red, with a minus) | This resident owes the household |
| **₹0.00** | Settled |

The sign is always shown, so the direction never depends on the colour.

**Household pool** is the collection pool. A positive pool balance means
residents owe the pool in aggregate. Every resident plus the pool always sums
to **zero** — if that ever fails, the screen says so rather than showing a
figure.

### Charged, outstanding, credit, funded

| Column | Means |
| --- | --- |
| **Charged** | Their total share of household expenses |
| **Settled** | How much of that a payment has covered |
| **Outstanding** | Charged minus settled — what is still owed |
| **Credit** | An overpayment held for them, carried forward |
| **Funded** | Expenses they paid for, plus provider advances |
| **Net balance** | All of the above combined |

### Suggested transfers

Advice, not a record. Nothing on that panel changes the accounts. When money
actually moves, record it as a payment.

---

## When something looks wrong

**Reconciliation** names the specific record at fault and says what to do:

| Message | What it means | What to do |
| --- | --- | --- |
| An expense's shares do not add up | Someone edited data outside the app | Open the expense and fix the allocation |
| An advance matches an expense but is not linked | A likely double credit | Link the advance, or clear the expense's *Paid by* |
| An expense is only partly funded by linked advances | The rest is treated as pool-funded | Record the remaining advance, or confirm it is right |
| A former resident still has an unsettled balance | They moved out owing or owed | Settle it, or record an adjustment documenting how it was resolved |

You can also run the same check from the terminal:

```bash
.venv/bin/python scripts/manage.py check
```

---

## Exports and printing

**Reports & exports** has ten reports: monthly household expenses, resident
breakdowns, payments and balances, provider advances, electricity consumption,
credits and adjustments, statement summaries, statement detail, the transaction
ledger, and the audit trail. Each previews on screen and downloads as CSV.

Every CSV carries each amount twice — the authoritative integer paise and a
plain rupee column for spreadsheets — with no currency symbol or digit
grouping, so the values parse cleanly.

A **statement export** comes from that statement's stored snapshot, so a
finalized statement always exports as it was finalized.

For a paper copy, open the statement and press **Print**: the navigation and
controls drop away.

---

## Looking something up

**Transactions** is one searchable ledger of expenses, payments, and
adjustments together. Filter by month, resident, category, type, or status.
Filtering by resident finds both their own transactions and the expenses they
hold a share of.

**Audit trail** records every change with its old value, new value, timestamp,
who made it, and the reason where one was required. It cannot be edited or
deleted.

**Reconciliation → Ledger entries** shows the individual signed entries behind
every balance, each naming the record it came from, so any figure on any screen
can be traced back to a row.
