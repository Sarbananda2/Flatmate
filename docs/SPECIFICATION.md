# Business and Calculation Specification — v1.0

> **Status.** Every rule below is implemented and covered by tests. Where the
> specification left a decision open, the application's current behaviour is
> stated alongside it, and the full list of open decisions is in
> [`docs/ACCOUNTING_RULES.md` §14](docs/ACCOUNTING_RULES.md#14-decisions-still-open).
>
> The authoritative description of what the application does is
> [`docs/ACCOUNTING_RULES.md`](docs/ACCOUNTING_RULES.md). This document records
> the business intent it was built from.

## 1. Purpose

Calculate household expenses, resident allocations, payments, advances, credits, and pooled settlement positions while preserving an auditable history.

## 2. Confirmed business rules

1. Each expense has a configurable period rule. At minimum: `PREVIOUS_MONTH`, `CURRENT_MONTH`, and `TRANSACTION_DATE`.
2. Electricity is mixed: measured individual usage, shared components, and separately allocated charges can coexist.
3. Smart-plug readings are provided as inputs.
4. Guests are not debtors. Guest electricity is assigned to the resident who brought the guest.
5. Grocery purchases are recorded individually and should be settled as soon as practicable. Monthly reconciliation must not charge already-settled purchases twice.
6. Unpaid balances and credits carry forward.
7. Payments are automatically allocated to the oldest outstanding charges.
8. Direct payments to providers are tracked separately from resident-to-resident/collector transfers.
9. Any unavoidable equal-split rounding remainder should not benefit the payer; the payer receives the lower share where the allocation rules permit it.
10. Monthly statements are explicitly finalized by the app operator.
11. Finalization starts a three-day correction window. After that, the statement becomes immutable. Later corrections are separate ledger adjustments.
12. Settlement is pooled: the system nets resident charges and advances and outputs each resident's net payable/receivable position.
13. The local application and business logic are in scope. Production infrastructure is not.

## 3. Core concepts

- **Expense definition:** reusable metadata such as Rent, Broadband, Electricity, or Groceries.
- **Expense instance:** an amount for a specific period/date.
- **Expense component:** a part of a compound expense with its own allocation rule. Example: individual AC usage and common electricity.
- **Allocation:** the amount assigned to one resident for one expense/component.
- **Payment:** money transferred by a resident toward their settlement; applied oldest-charge-first.
- **Provider advance:** a resident pays an external provider on behalf of the household. This creates a credit/receivable for that resident in pooled settlement.
- **Statement:** a calculation run for a settlement month, with draft/finalized/closed status and a snapshot.
- **Adjustment:** a separately recorded post-close correction; it does not rewrite the original statement.
- **Guest charge:** an allocation assigned to the host resident, never to the guest.

## 4. Pooled settlement formula

For each resident:

`net_position = provider_advances + other_household_credits - allocated_charges - applicable_debits`

- Positive net position: household owes the resident.
- Negative net position: resident owes the household/pool.
- Zero: settled at the pooled level.

A final transfer plan should match debtors' negative positions to creditors' positive positions. The engine currently calculates positions; transfer-plan optimization is a later feature.

Resident-to-collector payments reduce the payer's open charges through oldest-first allocation. They should not be counted as provider advances. If the collector then pays a provider, record that provider expense/payment separately, avoiding double counting.

## 5. Allocation methods

Supported in the prototype:
- Equal split among selected participants.
- Weighted split using non-negative integer weights.
- Explicit custom amounts that must sum exactly to the expense amount.

For equal-split rounding, the base share is assigned first. Remaining paise are distributed deterministically to non-payers before the payer, where possible, so the payer does not receive the extra paise. The sum of shares must always equal the total exactly.

For weighted splits, the prototype uses largest-remainder allocation and deterministic tie-breaking. The special payer rounding preference is currently applied only to equal splits; if the product requires the payer-preference rule for weighted/custom splits too, that behavior must be specified.

## 6. Electricity engine — separate plug-in module

The electricity calculation engine is intentionally outside the core expense/settlement engine. It will be implemented as an independent module with a stable input/output contract, then plugged into the main application.

The core application must not contain electricity-specific tariff or smart-plug calculation logic. It should treat the electricity module's output as an expense with itemized allocations, just like any other expense.

### Expected module inputs
- Settlement/consumption period.
- Raw smart-plug readings and their resident/device associations.
- Applicable tariff/rate information, if needed by the module.
- Common electricity amount and other bill components.
- Guest usage or guest cost attributed to a host resident, when provided.
- Allocation configuration and rounding policy.

### Expected module outputs
- Total electricity expense in integer paise.
- Itemized components (e.g., individual AC usage, common charges, GST/recharge fees, guest usage).
- Resident-level allocations, with the host resident charged for guest electricity.
- A calculation breakdown explaining how each component was derived.
- Validation warnings for missing readings, mismatched periods, or incomplete tariff inputs.

### Integration contract
The module should expose one clear function or service boundary, conceptually:

`calculate_electricity(input_data) -> ElectricityCalculationResult`

The result should contain:
- `total_amount_paise`
- `components`
- `allocations` (resident ID and amount in paise)
- `breakdown`
- `warnings`

The core app will validate that the component totals and resident allocations reconcile exactly to the returned total. The electricity module should not write directly to the resident ledger or create payment records; the core application owns persistence, statement lifecycle, payments, credits, and pooled settlement.

Smart-plug reading inputs and monetary rates remain separate. The module should retain enough calculation detail to explain the result later. Tariff conversion, rate schedules, taxes, and exact guest-reading treatment will be defined within the electricity module's own specification, not embedded into the core engine.

## 7. Grocery workflow

Each purchase records date, amount, payer, participants, allocation, and settlement state. Purchases should be settled promptly. A monthly statement can reconcile any still-open grocery balances, but must reference existing purchase records rather than creating duplicate charges.

## 8. Oldest-first payment allocation

A resident payment is applied to that resident's oldest open charge first, ordered by due date, then creation time, then stable charge ID. Any remainder becomes an unapplied credit. Credits carry forward.

## 9. Statement lifecycle

- `DRAFT`: editable and recalculates as inputs change.
- `FINALIZED_CORRECTION_WINDOW`: snapshot captured; correction window remains open for 72 hours from finalization.
- `CLOSED`: normal edits blocked; original snapshot immutable.
- Post-close mistakes are handled with separately recorded adjustments linked to the original statement.

The 72-hour interpretation is a proposed operational definition of “three days” and should be confirmed.

## 10. Proposed SQLite entities

- `apartment`
- `resident`
- `expense_definition`
- `expense_instance`
- `expense_component`
- `allocation`
- `electricity_calculation` (optional module output record/reference)
- `payment`
- `payment_application`
- `provider_advance`
- `ledger_adjustment`
- `monthly_statement`
- `statement_revision`

Amounts are stored as integer paise. Timestamps should be ISO-8601 with timezone information. Closed statement snapshots should be immutable by application logic and protected by database constraints/triggers where appropriate.

## 11. Decisions: resolved and still open

| # | Decision | Status |
|---|----------|--------|
| 1 | How smart-plug kWh becomes money (tariffs, time-of-use, taxes) | **Resolved by not doing it.** The actual utility bill and its itemised charges are authoritative; kWh are allocation weights only. The engine never reconstructs a tariff. |
| 2 | Whether guest usage is a separate reading or part of the host's | **Resolved.** Guest kWh is added to the host's own reading before calculation. No guest account is ever created. |
| 3 | How move-in/move-out dates affect fixed charges | **Decided 2026-10-11: nothing prorates.** A partial month never reduces a share; dates are recorded for reference only. See `docs/ACCOUNTING_RULES.md` §7. |
| 4 | Oldest-first: due date or charge date | **Resolved, confirmable.** Due date, then insertion sequence, then charge ID. The due date defaults to the last day of the settlement month. |
| 5 | A payment larger than open charges | **Resolved, confirmable.** It stays an unapplied credit, visible and carried forward, applied only by an explicit operator action. |
| 6 | Whether "three days" is 72 hours | **Implemented as 72 hours**, configurable per statement. The calendar-day reading is not implemented. |
| 7 | Which methods honour the payer rounding preference | **Partially resolved.** Fully specified and implemented for equal splits; applied as a tie-break only for weighted and measured. See §14.2. |
| 8 | Minimum pooled transfers | **Still open.** The planner is greedy and deterministic; it does not promise a globally minimal count. See §14.6. |

Two decisions were added during implementation and are also recorded in
§14: partial payment reversal (not supported — reverse in full, or adjust) and
grocery settlement state (modelled through charges and payments rather than a
separate flag).

## 12. Acceptance criteria

- Allocations always sum exactly to the expense amount.
- A guest is never created as a debtor.
- An expense already settled as an individual grocery purchase is not charged again in monthly reconciliation.
- Payments apply oldest-first and never exceed the amount applied to each charge.
- Provider advances increase the payer's pooled credit without creating a second expense.
- Pooled net positions sum to zero when all expenses and funding/advances are represented.
- Finalized statement snapshots remain unchanged by later adjustments.


## Electricity calculation module (implemented)

The electricity engine is a separate pure calculation module (`electricity_engine.py`) and does not mutate the ledger.

1. The actual utility bill total is authoritative. Money values are integer paise.
2. Itemized bill components may be entered separately; their amounts must sum exactly to the bill total.
3. Each component supports a configurable allocation method:
   - `usage`: allocate in proportion to each resident's aggregate smart-plug kWh.
   - `equal`: allocate equally.
   - `custom`: exact paise amounts or configurable weights per resident.
4. If no bill components are provided, the entire bill becomes one usage-based component.
5. Smart-plug readings are aggregated per resident. Unknown resident IDs, negative readings, duplicate component IDs, invalid money values, unsupported methods, and inconsistent totals are rejected.
6. Any guest kWh is included in the host resident's smart-plug reading; no separate guest resident is created.
7. Rounding uses integer arithmetic and largest remainders. If a payer is identified, the payer is last in tie-breaking for remainder paise, reflecting the agreed rounding benefit.
8. A positive usage-based bill with zero recorded usage is rejected rather than silently inventing an allocation.
9. The output includes component allocations and resident-level totals, with validation that both reconcile to the bill total.

Tariff reconstruction from kWh is intentionally not part of this module. The module allocates the actual bill and its charges, because the agreed model uses the provider's bill rather than an estimated rate.


## SQLite electricity expense-entry workflow (implemented)

`electricity_workflow.py` connects the pure calculation engine to SQLite. `create_electricity_expense` validates the apartment's residents and optional expense definition/statement, runs the electricity engine, and atomically saves:
- one `expense_instance` row for the actual utility bill;
- one `expense_component` row per bill line item (or one synthesized whole-bill component);
- `allocation` rows for each component/resident pair;
- `smart_plug_reading` rows with integer Wh values.

Money is entered to the workflow in integer paise. The terminal wizard accepts rupees with up to two decimal places and converts them to paise. kWh readings are stored as integer Wh and therefore support up to three decimal places. If validation or database insertion fails, all writes from that entry are rolled back. The workflow refuses to attach an expense to a statement that is not in `draft` status. It returns the engine's calculated allocations and provides `get_electricity_expense` for retrieving the saved record.

The interactive entry command is:

`python electricity_entry_cli.py --db apartment.sqlite --apartment <apartment_id>`

The database must already contain the apartment and resident records. This is a terminal workflow, not a graphical web form. Application-wide settlement, statement generation, and a web UI remain outside this increment.

A first-run setup wizard (`apartment_setup_cli.py`) creates an apartment and registers resident records, so the terminal electricity-entry flow can be used without manually inserting seed rows into SQLite.


## General ledger workflow (implemented, with explicit boundaries)

`ledger_workflow.py` provides SQLite persistence for dynamic expense definitions, monthly draft statements, manually allocated general expenses, payments, statement previews, finalization snapshots, and closing expired statements. `ledger_entry_cli.py` provides a terminal menu for these operations.

- General expense shares must contain every resident in the apartment and sum exactly to the expense total.
- Expenses can be linked to a dynamic expense definition and draft statement.
- `to_pool` payments apply against a resident's oldest outstanding allocations first; excess is returned as unapplied credit.
- `provider_advance` payments are tracked separately from `to_pool` payments.
- Finalization saves a snapshot and opens the agreed 72-hour correction window. Expired statements can be closed by the menu action.
- Expense entry is blocked for statements that are not draft.

Current boundary: recurring definitions are stored but not yet auto-instantiated; general expense allocations are entered manually; expense due dates currently fall back to expense dates for oldest-first payment applications. The full-ledger pooled reconciliation now includes cross-month charges, provider advances, resident-to-pool payments, refunds, and signed adjustments. Before relying on this prototype for final accounting, test it against actual historical examples. (Resident move-in/out proration was subsequently decided: nothing prorates. Due-date semantics were settled as the end of the settlement month.)


## Full-ledger pooled reconciliation (implemented)

`reconcile_pooled_settlement` calculates account positions across all recorded expenses and funding entries, including prior months. It includes resident allocation debits, expense funding credits, provider advances, resident-to-pool payments, refunds, signed `ledger_adjustment` rows, and the synthetic `__pool__` account. The resulting account positions are validated to sum to zero, then passed to the deterministic transfer planner. An optional `as_of_date` restricts dated expenses/payments and adjustment creation dates.

`record_adjustment` records signed, auditable corrections. Positive amounts credit the resident; negative amounts debit the resident. Provider advances can now reference an `expense_id` to avoid counting the same funding once from `expense_instance.payer_resident_id` and again from `payment`. Existing databases are migrated by adding the nullable `payment.expense_id` column when initialized.
