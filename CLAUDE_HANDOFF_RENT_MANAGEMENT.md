# Technical Design Specification & Claude Code Handoff: Equal Split Rent Management

**Feature:** Single Master Lease Equal Split Rent Management  
**Status:** Approved by User  
**Interactive Wireframe:** See `wireframe_rent_management.html` in artifact storage.  
**Role of this Document:** Implementation blueprint for Claude Code.

---

## 1. Executive Summary & Model Overview

The household operates under **Rental Model 1: Equal Split Single Master Lease**:
- **Single Legal Entity:** The apartment has one master lease agreement with the landlord (e.g., total monthly rent of ₹45,000.00).
- **Equal Liability:** The total lease obligation is divided equally across all active residents participating in the cycle ($N$ residents $\rightarrow 1/N$ each).
- **Accounting Period:** 
  - `service_period = current_month` (e.g., 2026-10 for October rent).
  - `settlement_period = current_month` (charged in the October statement).
  - Rent is paid in **advance** on or before the lease due date (e.g., 5th of each month).
- **Dual Outflow Architecture:**
  - **Internal Collection:** Residents pay their ₹15,000 share into the apartment pool.
  - **Landlord Disbursement:** The total ₹45,000 is transferred to the landlord either directly from the apartment pool account, or funded by one resident on behalf of the household (treated as a linked `provider_advance`).

---

## 2. Mathematical Rules & Accounting Invariants

Claude Code must strictly abide by the existing engine rules in `docs/ACCOUNTING_RULES.md`:

### 2.1 Integer Paise & Indivisible Remainder Allocation
- All values stored as `INTEGER amount_paise`.
- **Equal split base share:** `base_share = total_paise // n`.
- **Indivisible remainder paise:** `remainder = total_paise % n`.
- **Distribution of remainder:**
  - Distributed **one paisa each** in ascending `resident_id` order.
  - If a specific resident funded the expense via advance, that resident is placed **last** in the remainder tie-break (payer-last preference rule from Section 8 of `docs/ACCOUNTING_RULES.md`).
- **Sum invariant:** $\sum \text{resident\_share\_paise} \equiv \text{total\_monthly\_rent\_paise}$ (Zero paisa drift).

### 2.2 Partial Months: "Nothing Prorates"
- As established in Section 7 of `docs/ACCOUNTING_RULES.md`:
  - A resident active for any portion of the month is charged the **full equal share**.
  - No pro-rata fraction calculation.
  - If a resident is exempt (e.g., moved in right at month-end and agreed to skip), the operator explicitly toggles their participation off. The total is then divided equally among the remaining participants ($N-1$).

### 2.3 Funding and Double-Count Prevention
- If the landlord payout is funded by a resident (e.g. Aarav transfers ₹45,000 directly from his personal bank to the landlord):
  - A `provider_advance` payment row is created with `payment.expense_id` pointing to the rent `expense_instance`.
  - The `expense_instance.payer_resident_id` is set to Aarav.
  - Aarav is credited ₹45,000 in pool funding, while still being charged his ₹15,000 share.
  - The ledger net position for Aarav reflects $+₹30,000$ (he is owed ₹30,000 by the household pool).

### 2.4 Optional Landlord Receipts & Deferred Attachment
- **Real-World Fact:** Landlords almost never issue receipts immediately when rent is transferred; payments are executed on the 5th, and receipts (if any) arrive days later.
- **Lease Configuration Flag:** `requires_landlord_receipt` (0 or 1).
  - **When 0:** No landlord receipt is tracked or expected. Payout is validated solely via bank UTR / UPI reference ID.
  - **When 1:** Landlord receipt is expected, but **strictly optional at the time of payout**:
    - Operator can record the bank transfer on the due date without attaching a file.
    - Status surfaces a `[⚠ Receipt Pending]` indicator on the disbursed cycle.
    - An explicit `[+ Attach Receipt]` action allows uploading the receipt later via `POST /api/rent/cycle/{month}/landlord-receipt`.
    - Once attached, status updates to `[✓ Receipt Attached]` with download/replace actions.
- **Distinction Between Owner Receipt vs Tenant HRA Slip:**
  - *Owner Receipt:* Proof received from the landlord (optional, off by default, attachable later).
  - *Tenant HRA Slip:* Generated internally by AEM so residents can submit rent proof with the Landlord's PAN to their employer's tax portal.

### 2.5 Targeted Rent Payments (Rent-First Application)
- **Problem Statement:** In a shared flat, rent is a distinct, dedicated obligation. When a roommate transfers ₹15,000 for "October Rent", applying it oldest-first to a lingering September grocery balance causes October Rent to display `₹0 / Unpaid`, violating tenant expectations.
- **Rule:** If a payment is designated for rent, **it must apply directly to that rent charge first**.
- **Mechanics:**
  - `record_payment` accepts an optional `target_allocation_id` (or `target_expense_id`).
  - When provided:
    1. The payment settles the specified target allocation up to its open share.
    2. Any remaining excess funds flow oldest-first to the resident's other outstanding charges.
  - Expose an endpoint: `POST /api/rent/cycle/{month}/pay` taking `resident_id`, `amount_paise`, `payment_date`, `reference`, `note`.
  - In `frontend/src/pages/Rent.tsx`:
    - Each resident row with unpaid dues has an inline `[ + Record Payment ]` action.
    - Pre-fills the resident's exact outstanding rent share and applies it directly to their rent allocation.
    - Internal Collection and the resident's status immediately reflect "Paid".



---

## 3. Database Schema Migration (`aem/migrations/0005_rent_master_lease.sql`)

```sql
-- Migration 0005: Equal Split Master Lease Rent Management

CREATE TABLE IF NOT EXISTS rent_lease_config (
    config_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id),
    landlord_name TEXT NOT NULL,
    landlord_phone TEXT,
    landlord_email TEXT,
    landlord_pan TEXT,
    landlord_bank_account TEXT,
    landlord_bank_ifsc TEXT,
    landlord_upi_id TEXT,
    monthly_rent_paise INTEGER NOT NULL CHECK (monthly_rent_paise > 0),
    due_day_of_month INTEGER NOT NULL DEFAULT 5 CHECK (due_day_of_month BETWEEN 1 AND 28),
    requires_landlord_receipt INTEGER NOT NULL DEFAULT 0 CHECK (requires_landlord_receipt IN (0, 1)),
    created_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS rent_cycle (
    cycle_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id),
    settlement_month TEXT NOT NULL, -- YYYY-MM
    expense_id TEXT REFERENCES expense_instance(expense_id),
    total_rent_paise INTEGER NOT NULL CHECK (total_rent_paise > 0),
    participant_count INTEGER NOT NULL CHECK (participant_count > 0),
    disbursement_status TEXT NOT NULL CHECK (disbursement_status IN ('pending', 'disbursed')),
    disbursed_at TEXT,
    disbursement_reference TEXT, -- UTR / UPI txn ID
    disbursed_by TEXT NOT NULL CHECK (disbursed_by IN ('pool', 'resident_advance')),
    funding_resident_id TEXT REFERENCES resident(resident_id),
    created_at TEXT NOT NULL,
    UNIQUE(apartment_id, settlement_month)
);

CREATE TABLE IF NOT EXISTS rent_receipt (
    receipt_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES rent_cycle(cycle_id),
    resident_id TEXT NOT NULL REFERENCES resident(resident_id),
    settlement_month TEXT NOT NULL,
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    landlord_name TEXT NOT NULL,
    landlord_pan TEXT NOT NULL,
    verification_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(cycle_id, resident_id)
);
```

---

## 4. Backend Service Architecture (`aem/services/rent.py`)

### Required Service Functions:
1. `get_lease_config(conn, apartment_id) -> Optional[LeaseConfig]`
2. `save_lease_config(conn, apartment_id, payload) -> LeaseConfig`
3. `get_rent_cycle(conn, apartment_id, settlement_month) -> Optional[RentCycleSummary]`
4. `generate_monthly_rent_cycle(conn, apartment_id, settlement_month, excluded_resident_ids=None) -> RentCycleSummary`
   - Validates that statement for `settlement_month` is in `draft` status.
   - Computes equal split across active participating residents.
   - Creates an `expense_instance` under category `'Housing'` with description `"Rent for <Month> <Year>"`.
   - Populates `allocation` rows.
   - Creates `rent_cycle` row with `disbursement_status = 'pending'`.
5. `record_landlord_disbursement(conn, cycle_id, disbursement_mode, funding_resident_id, ref_no, disbursed_date) -> RentCycleSummary`
   - If `disbursement_mode == 'resident_advance'`:
     - Creates `provider_advance` payment linked to the rent `expense_id`.
   - Sets `disbursement_status = 'disbursed'`.
   - Generates immutable `rent_receipt` rows for each participant.
6. `get_resident_rent_receipt(conn, cycle_id, resident_id) -> RentReceiptPayload`
   - Returns structured receipt data with verification hash for HRA tax exemption printing.

---

## 5. API Endpoints (`aem/api/routers/rent.py`)

- `GET /api/rent/config` — Get master lease configuration.
- `POST /api/rent/config` — Update landlord details & monthly rent.
- `GET /api/rent/cycle/{settlement_month}` — Fetch rent cycle details, progress, and allocations.
- `POST /api/rent/cycle/{settlement_month}/generate` — Generate rent obligation for the month.
- `POST /api/rent/cycle/{settlement_month}/disburse` — Record landlord payout (Pool or Resident Advance).
- `GET /api/rent/cycle/{settlement_month}/receipt/{resident_id}` — Fetch printable HRA receipt.

---

## 6. Frontend Blueprint (`frontend/src/pages/Rent.tsx`)

### Production Component Mapping:
Claude Code must use the standard production UI components from `frontend/src/components/ui.tsx` and `frontend/src/components/Layout.tsx` rather than bespoke styles:
1. **Header & Navigation:** 
   - Uses standard `Layout` shell. Rent page is accessible via `/rent` or routed from the Activity Ledger / Month Hub.
   - Canvas toolbar uses standard `Settlement Period` navigator with `shiftMonth`.
2. **Metric Grid (`MiniStat`):**
   - Implemented as `<div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">` using `<MiniStat />`:
     - Contract Rent (`₹45,000.00`)
     - Equal Share (`₹15,000.00`)
     - Internal Collection (`₹30,000.00 · 66.7%`)
     - Landlord Due Date (`05 Oct 2026`)
3. **Panels (`Panel`):**
   - Wrap the allocation table and landlord profile inside `<Panel title="..." hint="..." actions="...">`.
4. **Pills & Status Badges (`Pill`):**
   - `<Pill tone="warning">Disbursement Pending</Pill>`
   - `<Pill tone="success">Disbursed</Pill>`
5. **Action Buttons:**
   - Use `BUTTON_PRIMARY` and `BUTTON_SECONDARY` tokens.
6. **Modals (`Modal`):**
   - Use `<Modal title="..." onClose="...">` from `frontend/src/components/Primitives.tsx`.


