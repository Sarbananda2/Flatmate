# Technical Handoff: Carry Forward / Opening Balances & Full UI Management

## 1. Context & User Requirements
The user requested:
1. **Household Expense Definitions Update (Completed)**:
   - Added `Water` as an 8th suggested/default essential expense definition alongside Rent, Electricity, Cooking Gas, House Help, Maintenance, Broadband, and Groceries.
   - Updated `aem/services/definitions.py`, `tests/test_setup_wizard.py`, `tests/test_end_to_end.py`, and `CHECKLIST.md`.
2. **Carry Forward Balances (In Progress)**:
   - **Both requirements were requested**:
     - (a) Historical **Opening Balances** per resident brought in from prior months before app adoption.
     - (b) **Brought Forward / Carry Forward reporting** on monthly statement snapshots (`Previous Balance + This Month's Charges - Payments ± Adjustments = Carry Forward`).
3. **UI Management**:
   - The user explicitly requested: *"we need a ui to manage these as well."*

---

## 2. Current Implementation State

### Completed Backend Work:
1. **Migration 0004 (`aem/migrations/0004_opening_balance.sql`)**:
   - Created `opening_balance` table:
     - `opening_balance_id TEXT PRIMARY KEY`
     - `apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id)`
     - `resident_id TEXT NOT NULL UNIQUE REFERENCES resident(resident_id)`
     - `amount_paise INTEGER NOT NULL CHECK (amount_paise <> 0)` (positive = owed to resident / credit; negative = owed by resident / debt)
     - `as_of_date TEXT NOT NULL`
     - `note TEXT`
     - Timestamps and audit fields.
   - Added `opening_balance_id` column to `payment_application`:
     - Triggers ensuring payment applications target either an allocation or an opening balance (not both).
     - Triggers enforcing application amounts never exceed the opening balance debt amount (`-(amount_paise)`).
2. **Service Layer (`aem/services/opening_balances.py`)**:
   - Implemented `set_opening_balance(...)`, `clear_opening_balance(...)`, `list_opening_balances(...)`, `read_opening_balance(...)`.
   - Charge ID helpers: `opening_charge_id(opening_balance_id)` (`opening:<id>`) and `split_charge_id(charge_id)`.
   - Validation rules: locked when any statement is finalized or closed; append-only audit logging.
3. **Oldest-First Payment Settlement (`aem/services/payments.py`)**:
   - Modified `_open_charges(...)` to include opening debt (`amount_paise < 0`) as the oldest charge due on `as_of_date` (sequence 0).
   - Added `_write_application(...)` and updated `reapply_unapplied_credit`, `reverse_payment`, and `read_payment` to handle applications linked to `opening_balance_id`.
   - Updated `resident_charge_ledger` to prepend opening balance as an initial ledger item.

---

## 3. Remaining Tasks for Claude Code

### Task 1: Complete Backend Integration for Opening Balances & Carry Forward
1. **Reconciliation Integration (`aem/services/reconciliation.py`)**:
   - In `build_ledger_entries(...)`:
     - Add `ENTRY_OPENING_BALANCE = "opening_balance"` ledger entry for each resident row in `opening_balance` (and counter entry on `POOL_ACCOUNT_ID`), date = `as_of_date`.
   - In `_settlement_view(...)`:
     - Include opening debts (`-ob.amount_paise`) in `allocated_charges_paise` and payment applications where `pa.opening_balance_id IS NOT NULL` in `settled_paise`, so `outstanding_charges_paise` reflects remaining opening debt.
     - Include opening credits in `provider_credits` or dedicated opening credit bucket so that net position cross-check verifies:
       `net = unapplied_credit - outstanding_charges + provider_credits + opening_credits + adjustments ... == positions[rid]`
2. **Monthly Statement Carry-Forward Breakdown (`aem/services/statements.py`)**:
   - In `statement_snapshot(...)`:
     - Calculate for each resident:
       - `brought_forward_paise`: Net balance as of the start of the settlement month.
       - `current_month_charges_paise`: Charges incurred in this statement.
       - `current_month_payments_paise`: Payments applied or made this month.
       - `current_month_adjustments_paise`: Adjustments effective in this month.
       - `carried_forward_paise`: Net balance carrying forward into next month.
3. **API Router & Schemas (`aem/api/routers/residents.py` or new `aem/api/routers/opening_balances.py`)**:
   - Endpoints:
     - `GET /api/opening-balances` -> list opening balances for the active apartment.
     - `POST /api/opening-balances` -> create or update resident opening balance.
     - `DELETE /api/opening-balances/{resident_id}` -> clear opening balance.
   - Pydantic models in `aem/schemas/models.py`.

---

### Task 2: UI Implementation (Frontend)
The user explicitly requested UI support to manage opening balances and see carry-forward amounts.

1. **API Client & Types (`frontend/src/api/types.ts` & `frontend/src/api/client.ts`)**:
   - Add TypeScript interface `OpeningBalance`:
     ```ts
     export interface OpeningBalance {
       opening_balance_id: string;
       resident_id: string;
       display_name: string;
       amount_paise: number;
       direction: 'owed_to_resident' | 'owed_by_resident';
       as_of_date: string;
       note?: string;
       settled_paise: number;
       outstanding_paise: number;
     }
     ```
   - Add client API methods: `getOpeningBalances()`, `setOpeningBalance(payload)`, `deleteOpeningBalance(residentId)`.
2. **Resident Details & Setup UI**:
   - In `frontend/src/pages/Residents.tsx` or `ResidentDetail.tsx`:
     - Add an **"Opening Balance / Carry Forward"** card or modal.
     - Allow entering: Amount (₹), Direction (Resident owes pool vs Pool owes resident), As-of Date, Note.
     - Display current status (Settled vs Outstanding).
   - In `frontend/src/pages/Setup.tsx` or `SetupWizard.tsx`:
     - Surface the ability to enter opening balances when setting up residents.
3. **Statement Detail Carry-Forward View (`frontend/src/pages/StatementDetail.tsx`)**:
   - Add the carry-forward reconciliation breakdown card:
     - Table showing per-resident:
       `Brought Forward | This Month's Share | Payments & Advances | Adjustments | Carried Forward`
4. **Design Harmony**:
   - Maintain consistency with `DESIGN_HANDOFF_FOR_CLAUDE.md` (topbar ribbon, Lora typography, Tabular Monospace figures, high-trust accounting aesthetic).

