# CLAUDE HANDOFF: Landlord & Household Pool Payment Instances (Approach 1)

**Date:** 2026-10-16  
**Author:** Lead Architect  
**Status:** Ready for Implementation  
**Target Modules:**
- `aem/persistence/postgres/migrations/0015_payment_instances_landlord_and_pool.sql`
- `aem/persistence/repositories/apartments.py`, `aem/persistence/repositories/deposits.py`
- `aem/services/apartments.py`, `aem/services/deposits.py`, `aem/services/reconciliation.py`, `aem/services/sharing.py`, `aem/services/resident_statements.py`
- `aem/schemas/models.py`
- `frontend/src/api/types.ts`
- `frontend/src/pages/Payments.tsx`
- `frontend/src/pages/services/SecurityDeposits.tsx`
- `frontend/src/pages/services/Rent.tsx`
- `frontend/src/pages/Dashboard.tsx`

---

## 1. Architectural Objective

Formalize two permanent system payment/counterparty instances in every apartment alongside human **Residents (`res_*`)**:

1. **The Landlord Instance (`__landlord__`)**:
   - Represents the property owner / master lease landlord (backed by the apartment's active lease `landlord_*` fields and synced `role = 'owner'` contact in `household_contact`).
   - Holds its own payment details: **Name**, **Bank Account**, **IFSC**, **UPI ID** (plus Phone, Email, PAN).
2. **The Household Pool Instance (`__pool__`)**:
   - Represents the household's common collection & clearing pool (`POOL_ACCOUNT_ID = "__pool__"`).
   - Holds its own payment details on the `apartment` record: **Pool Name** (defaults to `"Household Pool"`), **Bank Account**, **IFSC**, and **UPI ID**.
   - Managed via a dedicated read-only **Household Pool Account** card with an **Edit** modal on the **Payments & Credits (`/payments`)** page (following **Rule U1, U2, U4, U5**).

Both instances act as structured counterparties with visible payment destination cards (**"Transfer to"**) across **Security Deposits**, **Rent Collection & Disbursement**, **Payments & Credits**, and **Statement / WhatsApp UPI Settlement Links**.

---

## 2. Database Schema & Migration (`0015_payment_instances_landlord_and_pool.sql`)

Create `aem/persistence/postgres/migrations/0015_payment_instances_landlord_and_pool.sql`:

```sql
-- Formalize the Household Pool payment instance on apartment and explicit
-- counterparty kinds ('resident', 'landlord', 'pool') on security deposits.

ALTER TABLE apartment
    ADD COLUMN pool_name TEXT NOT NULL DEFAULT 'Household Pool'
        CHECK (length(trim(pool_name)) > 0),
    ADD COLUMN pool_bank_account TEXT
        CHECK (pool_bank_account IS NULL OR pool_bank_account ~ '^[0-9]{9,18}$'),
    ADD COLUMN pool_bank_ifsc TEXT
        CHECK (pool_bank_ifsc IS NULL OR pool_bank_ifsc ~ '^[A-Z]{4}0[A-Z0-9]{6}$'),
    ADD COLUMN pool_upi_id TEXT
        CHECK (pool_upi_id IS NULL
               OR pool_upi_id ~ '^[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}$');

ALTER TABLE household_deposit_slot
    ADD COLUMN paid_to_party TEXT
        CHECK (paid_to_party IS NULL OR paid_to_party IN ('resident', 'landlord', 'pool'));

-- Backfill existing funded slots: if paid_to_resident_id is set, it went to a resident;
-- otherwise funded slots went to the landlord.
UPDATE household_deposit_slot
   SET paid_to_party = CASE
       WHEN paid_to_resident_id IS NOT NULL THEN 'resident'
       WHEN funded_amount_paise > 0 THEN 'landlord'
       ELSE NULL
   END
 WHERE paid_to_party IS NULL;

-- Note: household_deposit_transaction is append-only (UPDATE is revoked for runtime role),
-- so only add the nullable column; historical rows with NULL counterparty_party are
-- resolved in the service layer.
ALTER TABLE household_deposit_transaction
    ADD COLUMN counterparty_party TEXT
        CHECK (counterparty_party IS NULL
               OR counterparty_party IN ('resident', 'landlord', 'pool'));
```

---

## 3. Backend Service & Schema Specifications

### 3.1 Apartment Service & Repository (`aem/services/apartments.py`, `aem/persistence/repositories/apartments.py`, `aem/schemas/models.py`)
1. **Editable Pool Fields on `apartment`:**
   - Extend `EDITABLE_FIELDS` in `aem/services/apartments.py` to include:
     - `pool_name` (non-empty text up to 200 chars; if omitted on PATCH, keep existing; if given as `None` or empty string, reset to `'Household Pool'`).
     - `pool_bank_account` (validated with the same 9–18 digit rule as `property.bank_account`).
     - `pool_bank_ifsc` (validated & uppercased with the same `^[A-Z]{4}0[A-Z0-9]{6}$` rule as `property.ifsc_code`).
     - `pool_upi_id` (validated with the same `name@bank` rule as `property.upi_id`).
   - Update `ApartmentsRepository.update` in `aem/persistence/repositories/apartments.py` to persist `pool_name`, `pool_bank_account`, `pool_bank_ifsc`, and `pool_upi_id`.
   - Extend `ApartmentUpdate` and `ApartmentResponse` in `aem/schemas/models.py` with:
     - `pool_name: str | None = Field(None, min_length=1, max_length=200)`
     - `pool_bank_account: str | None = Field(None, max_length=18)`
     - `pool_bank_ifsc: str | None = Field(None, max_length=13)`
     - `pool_upi_id: str | None = Field(None, max_length=320)`

### 3.2 Security Deposits Service (`aem/services/deposits.py`, `aem/persistence/repositories/deposits.py`, `aem/schemas/models.py`)
1. **Two Payment Instances Exposed in `deposit_summary` (`_summary`):**
   - Include a `payment_instances` object (or `landlord_instance` and `pool_instance`) in `_summary(uow, apartment_id)` so the frontend has both instances' payment details ready:
     ```python
     "landlord_instance": {
         "instance_id": "__landlord__",
         "party": "landlord",
         "name": lease["landlord_name"] if lease else "Landlord",
         "bank_account": lease["landlord_bank_account"] if lease else None,
         "bank_ifsc": lease["landlord_bank_ifsc"] if lease else None,
         "upi_id": lease["landlord_upi_id"] if lease else None,
     },
     "pool_instance": {
         "instance_id": "__pool__",
         "party": "pool",
         "name": apartment.get("pool_name") or "Household Pool",
         "bank_account": apartment.get("pool_bank_account"),
         "bank_ifsc": apartment.get("pool_bank_ifsc"),
         "upi_id": apartment.get("pool_upi_id"),
     },
     "primary_signatory_upi_id": (residents.get(signatory_id) or {}).get("upi_id"),
     ```
2. **Explicit Counterparty (`paid_to_party` / `counterparty_party`) on Deposit Slots & Transactions:**
   - Allowed `paid_to_party` values: `"resident" | "landlord" | "pool"`.
   - In `initialize_or_update_slots`:
     - When `fronted` is `True` (the Primary Signatory's own slot under `primary_signatory`), set `paid_to_party="landlord"` (since the signatory paid the landlord).
   - In `record_slot_funding`:
     - Accept optional `paid_to_party: str | None = None` in `DepositSlotFundingRequest` and `record_slot_funding(...)`.
     - **Under `primary_signatory` mode:**
       - Reimbursements always go to the primary signatory: `paid_to_party = "resident"` and `paid_to = signatory_id`. (If caller passed `paid_to_party` other than `None` or `"resident"`, raise `ValidationError("Reimbursements go to the primary signatory.", details={"field": "paid_to_party"})`).
     - **Under `direct_co_tenants` mode:**
       - If `paid_to_resident_id` is given: `paid_to_party = "resident"`.
       - Otherwise `paid_to_party` must be `"landlord"` or `"pool"` (defaulting to `"landlord"` if omitted so existing API callers and tests continue to pass seamlessly).
       - Set `paid_to_resident_id = None` when `paid_to_party in ("landlord", "pool")`.
     - Save `paid_to_party` on `household_deposit_slot` and `counterparty_party=paid_to_party` on `household_deposit_transaction`.
   - In `settle_slot_in` (`vacate_and_settle_slot`):
     - When `settlement_path == "direct_transfer"`: `counterparty_party = "resident"`, `counterparty_resident_id = incoming_id`.
     - When `settlement_path == "signatory_refund"`:
       - Under `primary_signatory`: `counterparty_party = "resident"`, `counterparty_resident_id = signatory_id`.
       - Under `direct_co_tenants`: `counterparty_party = slot.get("paid_to_party") or "landlord"`, `counterparty_resident_id = slot.get("paid_to_resident_id")`.
       - Record `counterparty_party` on both `move_out_deduction` and `move_out_refund` transactions!
   - **Display Name Resolution in `_summary`:**
     - Helper `party_display_name(party: str | None, resident_id: str | None) -> str | None`:
       - If `resident_id`: returns `name(resident_id)`
       - If `party == "pool"`: returns `apartment.get("pool_name") or "Household Pool"`
       - If `party == "landlord"`: returns `f"Landlord ({lease['landlord_name']})" if lease and lease.get("landlord_name") else "Landlord"`
     - On each slot in `_summary["slots"]`:
       - Resolve `effective_party = slot.get("paid_to_party") or ("resident" if slot.get("paid_to_resident_id") else ("landlord" if slot["funded_amount_paise"] > 0 else None))`
       - Return `"paid_to_party": effective_party` and `"paid_to_display_name": party_display_name(effective_party, slot.get("paid_to_resident_id"))`.
     - On each transaction in `_summary["slots"][i]["transactions"]`:
       - Resolve `effective_tx_party = tx.get("counterparty_party") or ("resident" if tx.get("counterparty_resident_id") else ("resident" if mode == "primary_signatory" and signatory_id else (slot.get("paid_to_party") or "landlord")))`
       - Return `"counterparty_party": effective_tx_party` and `"counterparty_display_name": party_display_name(effective_tx_party, tx.get("counterparty_resident_id"))`.

### 3.3 Reconciliation, Sharing & Resident Statements (`reconciliation.py`, `sharing.py`, `resident_statements.py`)
1. **`aem/services/reconciliation.py`:**
   - Read `apartment = get_apartment(uow, apartment_id)` and use `pool_display_name = apartment.get("pool_name") or "Household pool"` for the `__pool__` account's `display_name` (or keep `"Household pool"` if default) and include `"upi_id": apartment.get("pool_upi_id")`, `"bank_account": apartment.get("pool_bank_account")`, `"bank_ifsc": apartment.get("pool_bank_ifsc")` on the `POOL_ACCOUNT_ID` account row in `account_rows`.
2. **`aem/services/sharing.py`:**
   - In `whatsapp_summary`, if `apartment.get("pool_upi_id")` is set, add `upi_ids[POOL_ACCOUNT_ID] = apartment["pool_upi_id"]`!
   - Now any suggested transfer where `transfer["to_account"] == "__pool__"` automatically gets `upi_id` and a `upi://pay` link (`upi_uri`) in the statement sharing summary!
3. **`aem/services/resident_statements.py`:**
   - In `_build`, when a resident owes money (`net_due > 0`, i.e., `Owed to Pool`), prefer the Household Pool's UPI ID (`apartment.get("pool_upi_id")`) and name (`apartment.get("pool_name") or "Household Pool"`) as `recipient_upi_id` and `recipient_name`, falling back to `signatory` only when `pool_upi_id` is not set.

---

## 4. Frontend Specifications

### 4.0 Top-Ribbon Navigation (`frontend/src/components/Layout.tsx`)
1. **Add `"Payment Pools"` to the Top Navigation Ribbon:**
   - Extend `Section` union type:
     ```ts
     type Section = 'hub' | 'housing' | 'services' | 'ledger' | 'residents' | 'pools' | 'statements' | 'settings'
     ```
   - Insert `{ to: '/payments', label: 'Payment Pools', section: 'pools' }` into `NAV_TABS` between `Residents` and `Statements`:
     ```ts
     const NAV_TABS: Array<{ to: string; label: string; section: Section }> = [
       { to: '/', label: 'Month Hub', section: 'hub' },
       { to: '/housing', label: 'Housing', section: 'housing' },
       { to: '/services', label: 'Services & Utilities', section: 'services' },
       { to: '/transactions', label: 'Activity Ledger', section: 'ledger' },
       { to: '/residents', label: 'Residents', section: 'residents' },
       { to: '/payments', label: 'Payment Pools', section: 'pools' },
       { to: '/statements', label: 'Statements', section: 'statements' },
     ]
     ```
   - In `SECTION_PREFIXES`, map `['/payments', 'pools']` (instead of `'ledger'`).
   - Include `section === 'pools'` in `isConfigPage` (and `section !== 'pools'` in `showRecord`) so the global settlement month bar and generic `Record Transaction` button do not compete with `/payments`'s own header actions (`Record a payment`, `Paid a provider`, `Export CSV`).

### 4.1 Payment Pools Page — Bank Statement & Passbook Redesign (`frontend/src/pages/Payments.tsx` & Backend `/api/payments/pools/statement`)

The `Payment Pools` (`/payments`) screen must function as a **proper bank statement / passbook** for both the **Household Pool (House Kitty)** and the **Landlord Account**, while fixing all layout, overflow, and clutter issues:

#### 1. Backend Statement Endpoint (`GET /api/payments/pools/statement`)
Add a read-only service function `get_payment_pools_statement(db, apartment_id)` in `aem/services/payments.py` (or a dedicated helper) exposed at `GET /api/payments/pools/statement` in `aem/api/routers/payments.py` that returns deterministic, chronological bank-statement ledgers for both payment instances:

- **`pool_statement` (House Kitty Passbook):**
  - Collects every cash movement into (`direction: "in"`) and out of (`direction: "out"`) the House Kitty:
    1. **Resident Kitty Payments (`direction: "in"`):** Active rows from `payment` where `payment_kind == "to_pool"`.
       - `category`: `"Rent Collection"` if `note` starts with `"Rent for "` or applications settle a rent expense, else `"Kitty Payment"`.
       - `counterparty_name`: `payer_display_name`, `counterparty_resident_id`: `payer_resident_id`.
       - `description`: `payment.note` or `"Payment into House Kitty"`.
       - `reference`: `payment.reference`, `payment_mode`: `payment.payment_mode`, `payment_id`: `payment.payment_id`.
    2. **Resident Refunds from Kitty (`direction: "out"`):** Active rows from `payment` where `payment_kind == "refund"`.
       - `category`: `"Resident Refund"`, `counterparty_name`: `payer_display_name`, `description`: `payment.note` or `"Refund from House Kitty"`.
       - `payment_id`: `payment.payment_id`.
    3. **Household Expenses Paid from Kitty (`direction: "out"`):** Rows from `expense_instance` where:
       - `payer_resident_id IS NULL`
       - has no linked `provider_advance`
       - is NOT an expense associated with a `rent_cycle` (so rent paid from kitty is not double-counted).
       - `category`: `"House Expense"`, `counterparty_name`: `provider_name` or `"House Expense"`, `description`: `description`, `amount_paise`: `amount_paise`, `date`: `expense_date`, `source_type`: `"expense"`.
    4. **Landlord Rent Payouts from Kitty (`direction: "out"`):** Disbursed rows from `rent_cycle` where `disbursement_status == "disbursed"` and `disbursed_by == "pool"`.
       - `date`: `disbursed_at`, `category`: `"Rent Payout"`, `counterparty_name`: `config.landlord_name` (or `"Landlord"`), `description`: `f"Rent payout · {month_label(settlement_month)}"`, `reference`: `disbursement_reference`, `amount_paise`: `total_rent_paise`.
    5. **Security Deposit Movements via Kitty (`direction: "in"` or `"out"`):** Rows from `household_deposit_transaction` where effective counterparty is `"pool"`:
       - `tx_type == "fund"`: `direction: "in"`, `category: "Security Deposit"`, `counterparty_name`: resident name, `description`: `f"Security deposit funded · {room_number}"`.
       - `tx_type == "landlord_refund"`: `direction: "in"`, `category: "Deposit Refund"`, `counterparty_name`: landlord name, `description`: `f"Deposit refunded by landlord · {room_number}"`.
       - `tx_type == "signatory_refund"`: `direction: "out"`, `category: "Deposit Refund"`, `counterparty_name`: resident name, `description`: `f"Security deposit refunded · {room_number}"`.
  - Sort chronologically (`date ASC, seq ASC`) to compute `balance_paise` (running balance after each entry: `running += amount_paise if direction == "in" else -amount_paise`), then return items newest-first (`date DESC, seq DESC`) along with:
    - `total_in_paise`: sum of `"in"` entries
    - `total_out_paise`: sum of `"out"` entries
    - `closing_balance_paise`: `total_in_paise - total_out_paise`

- **`landlord_statement` (Landlord Account Passbook):**
  - Collects every payment made to the Landlord (`direction: "in"`, i.e. credited/paid to Landlord) and every refund or deduction by the Landlord (`direction: "out"`):
    1. **Monthly Rent Disbursed to Landlord (`direction: "in"`):** Disbursed rows from `rent_cycle` where `disbursement_status == "disbursed"`.
       - `date`: `disbursed_at`, `category`: `"Rent Payout"`, `counterparty_name`: `"House Kitty"` if `disbursed_by == "pool"` else `f"{funding_resident_name} (Advance)"`, `description`: `f"Monthly rent · {month_label(settlement_month)}"`, `reference`: `disbursement_reference`, `amount_paise`: `total_rent_paise`, `payment_id`: `funding_payment_id`.
    2. **Other Direct Provider Advances to Landlord (`direction: "in"`):** Active rows from `payment` where `payment_kind == "provider_advance"`, `payment_id` is not already a `rent_cycle.funding_payment_id`, and `payee_or_provider` matches the landlord's name (case-insensitive).
       - `date`: `payment_date`, `category`: `"Direct Advance"`, `counterparty_name`: `payer_display_name`, `description`: `payment.note` or `linked_expense_description` or `"Direct advance to Landlord"`, `reference`: `payment.reference`, `payment_id`: `payment.payment_id`.
    3. **Security Deposit Movements with Landlord (`direction: "in"` or `"out"`):** Rows from `household_deposit_transaction` where effective counterparty is `"landlord"` or `tx_type in ("landlord_refund", "deduction")`:
       - `tx_type == "fund"`: `direction: "in"`, `category: "Security Deposit"`, `counterparty_name`: resident name (or `"Primary Signatory"`), `description`: `f"Security deposit · {room_number}"`.
       - `tx_type == "landlord_refund"`: `direction: "out"`, `category: "Deposit Refund"`, `counterparty_name`: resident name / pool, `description`: `f"Deposit refunded by landlord · {room_number}"`.
       - `tx_type == "deduction"`: `direction: "out"`, `category: "Deposit Deduction"`, `counterparty_name`: resident name, `description`: `f"Deposit deduction · {room_number}"`.
  - Compute running cumulative balance (`balance_paise`) chronologically and return items newest-first along with:
    - `total_rent_paid_paise`: sum of rent payouts to Landlord
    - `deposit_held_paise`: net security deposit held by Landlord (`deposit funded - landlord refunds - deductions`)
    - `total_in_paise`: total paid to Landlord
    - `total_out_paise`: total refunded/deducted by Landlord
    - `closing_balance_paise`: `total_in_paise - total_out_paise`

#### 2. Frontend UI Fixes & Bank Statement Layout (`frontend/src/pages/Payments.tsx`, `Layout.tsx`, `index.css`)

1. **Replace the Permanent Blue Notice Banner with a `"How it works"` Pop-Up Modal:**
   - Remove the inline `<Callout tone="info" title="These are four different things">` from the page body.
   - Add a `"How it works"` button (with `HelpCircle` icon, `BUTTON_SECONDARY`) in the page header actions that opens a `<Modal title="How Payment Pools & Transfers Work" onClose={...}>` containing the explanation of `Into the pool`, `Paid a provider`, `Refund`, and `Adjustment`, with a `Close` button in the footer.

2. **Side-by-Side Payment Instance Cards (`grid-cols-1 md:grid-cols-2 gap-4`) with Live Account Balances:**
   - Change the wrapper grid from `lg:grid-cols-2` to `grid-cols-1 md:grid-cols-2 gap-4` so **`House Kitty Account`** and **`Landlord Account`** sit side-by-side on all standard laptop/desktop viewports (`>= 768px`).
   - Use concise `Edit` button text per Rule U5 (`Edit` visible label with `aria-label="Edit house kitty"` / `aria-label="Edit landlord"`).
   - At the bottom of **`HouseholdPoolCard`** (`House Kitty Account`), render a compact 3-column account summary strip (`border-t border-[var(--border-subtle)] pt-3 mt-1 grid grid-cols-3 gap-2 text-xs`):
     - `Total In (+)`: green tabular `formatInr(poolStatement.total_in_paise)`
     - `Total Out (−)`: red/amber tabular `formatInr(poolStatement.total_out_paise)`
     - `Kitty Balance`: bold tabular `formatInr(poolStatement.closing_balance_paise)`
   - At the bottom of **`LandlordAccountCard`**, render the matching 3-column summary strip:
     - `Rent Paid`: tabular `formatInr(landlordStatement.total_rent_paid_paise)`
     - `Deposit Held`: tabular `formatInr(landlordStatement.deposit_held_paise)`
     - `Total Paid`: bold tabular `formatInr(landlordStatement.total_in_paise)`

3. **Bank Statement Tabs & Collapsible Filters:**
   - Primary tabs (`role="tablist"`, `aria-label="Payment views"`):
     1. **`House Kitty Statement`** (`'pool_statement'`) — default active tab, showing the passbook statement for the House Kitty (`__pool__`).
     2. **`Landlord Statement`** (`'landlord_statement'`) — showing the passbook statement for the Landlord Account (`__landlord__`).
     3. **`All Payments`** (`'payments'`) — the raw resident payment records (`to_pool`, `provider_advance`, `refund`).
     4. **`Unapplied Credits (${count})`** (`'credits'`)
     5. **`Provider Advances (${count})`** (`'advances'`)
     6. **`Adjustments (${count})`** (`'adjustments'`)
   - **Collapsible Filters (like `Transactions.tsx`):**
     - Remove the standalone `<Card title="Filters">` box that permanently consumes vertical space.
     - Place a `Filters` toggle button (`SlidersHorizontal` icon) in the statement/table panel header that expands/collapses the filter bar (`Search`, `Resident`, `Kind / Direction`, `Status`).

4. **Summary-First Bank Statement & Payments Tables (Rule U3 & Rule U7 — Zero Horizontal Overflow):**
   - For **`House Kitty Statement`** and **`Landlord Statement`**, render a clean 6-column passbook table (`table.data`):
     - `Date` (`nowrap tabular`)
     - `Particulars` (Category badge/pill + single-line description)
     - `Counterparty` (Single-line `nowrap` resident or landlord name)
     - `In / Credit (+)` (`num` — green `+₹...` when `direction === 'in'`, `—` when `'out'`)
     - `Out / Debit (−)` (`num` — red `−₹...` when `direction === 'out'`, `—` when `'in'`)
     - `Balance` (`num` — bold tabular running `balance_paise`)
   - Clicking any row in the statement table (or pressing `Enter`/`Space` on its row trigger button) opens a **Read-Only Statement Entry Detail Modal** (Rule U7) displaying all atomic fields (`Date`, `Account`, `Category`, `Description`, `Counterparty`, `Direction`, `Amount`, `Running Balance`, `Payment Mode`, `Reference / UTR`), and if `entry.payment_id` is present, a button to view full payment application details or **Reverse** the payment.
   - For the **`All Payments`** tab (`tab === 'payments'`), also enforce **Rule U7 (Summary-First Table)** so there is **no horizontal scrollbar and no cut-off columns**:
     - Show 6 clean summary columns: `Date`, `Resident`, `Kind`, `Counterparty`, `Amount`, `Status`.
     - Move `Applied`, `Credit left`, `funds: ...`, `Reference`, `Note`, and the `Reverse` action into the on-click `PaymentDetailDialog` (opened by clicking any payment row, with `Reverse` button right inside the modal footer when `status === 'active'`, or a compact single action cell that never overflows).

5. **Fix Card Footer Double-Border & Top-Ribbon Fit (`frontend/src/index.css` & `frontend/src/components/Layout.tsx`):**
   - In `frontend/src/index.css`: add `.card-footer > .pagination { width: 100%; border-top: none; padding: 0; }` so `<Pagination>` inside `<Card footer={...}>` never draws a second short top border or shrinks to text width.
   - In `frontend/src/components/Layout.tsx`: adjust `NAV_TABS` link padding (`px-2 md:px-2.5 lg:px-3 py-1.5`) so all 7 tabs fit cleanly alongside the apartment brand name on desktop.
### 4.2 Security Deposits (`frontend/src/pages/services/SecurityDeposits.tsx`)
1. **Separate `Paid To` (Instance) from `Payment Method` (Channel) in `FundingModal` (`Record Deposit Payment`):**
   - In `FundingModal`:
     - **Paid To (`paid_to_party`):**
       - When `summary.funding_mode === 'primary_signatory'`: locked to Primary Signatory (`summary.primary_signatory_name`), and show a **"Transfer to: {summary.primary_signatory_name}"** destination box with their UPI ID (`summary.primary_signatory_upi_id`) if on file.
       - When `summary.funding_mode === 'direct_co_tenants'`: render a `SelectField` labeled **"Paid to"** with two instance options:
         1. `landlord` — `Landlord (${summary.landlord_instance.name})`
         2. `pool` — `Household Pool (${summary.pool_instance.name})`
       - Render a **"Transfer to"** destination box right above the form fields showing the selected instance's (`landlord_instance` or `pool_instance`) Name, Bank Account, IFSC, and copyable UPI ID!
     - **Payment Method (`payment_method`):**
       - Show only actual transfer channels in the dropdown: `UPI` (`'upi'`), `Bank transfer` (`'bank_transfer'`), `Cash` (`'cash'`). Default to `'upi'`. (Keep `direct_to_landlord: 'Direct to landlord'` in `METHOD_LABELS` only for rendering any legacy rows).
     - Submit `paid_to_party: fronted ? 'resident' : paidToParty` and `paid_to_resident_id: fronted ? summary.primary_signatory_id : null` to `POST /api/rent/deposits/slots/{slot_id}/fund`.
2. **Accurate Instance Names in `DepositDetailsModal`, `VacateModal`, and `DepositTransactionModal`:**
   - In `DepositDetailsModal`:
     - `Paid To` displays `slot.paid_to_display_name ?? (slot.is_primary_signatory ? `Landlord (${summary.landlord_instance.name})` : '—')`.
   - In `VacateModal`:
     - Under `signatory_refund`, `refunder` displays `slot.paid_to_display_name ?? (summary.funding_mode === 'primary_signatory' ? summary.primary_signatory_name : summary.landlord_instance.name)`.
   - In `txParties`:
     - `holder` uses `tx.counterparty_display_name`, which the backend now populates for `'resident'`, `'landlord'`, and `'pool'`!

### 4.3 Monthly Rent Collection (`frontend/src/pages/services/Rent.tsx`)
1. **Household Pool Destination Box in `RentPaymentModal` (`Record Rent Payment`):**
   - Pass `apartment` (from `useApp()`) into `RentPaymentModal`.
   - At the top of `RentPaymentModal`, render a **"Transfer to: {apartment.pool_name || 'Household Pool'}"** destination section (matching `PayoutModal`'s "Transfer to: {config.landlord_name}" box!) displaying the Household Pool's Bank Account, IFSC, and copyable UPI ID (or a link to `/payments` to add them if none are configured).

### 4.4 Dashboard Settlement (`frontend/src/pages/Dashboard.tsx`)
1. In `SettleDialog` (and/or the transfer row), when `transfer.to_account === poolAccountId` and the pool account has a `upi_id`, display the Household Pool's UPI ID with a `CopyButton` so the resident can copy it right from the settlement dialog.

---

## 5. Verification & Test Suite Requirements

1. **Backend (`pytest`):**
   - Test `GET /api/payments/pools/statement` verifying:
     - `pool_statement` returns chronological inflows (pool payments, deposit funds to pool) and outflows (rent payouts by pool, refunds) with running balance and correct totals.
     - `landlord_statement` returns rent payouts, direct advances, and deposit transactions for landlord with cumulative totals.
   - Run the full backend test suite (`pytest`) and ensure 0 regressions.
2. **Frontend (`npm test` & `npm run build`):**
   - Add/update tests in `Payments.test.tsx`, `SecurityDeposits.test.tsx`, and `Rent.test.tsx` covering:
     - `House Kitty Statement` and `Landlord Statement` passbook rendering with running balance.
     - Opening the `How it works` info modal and closing it.
     - On-click detail modal for statement rows and payment rows.
   - Run `npm test` and `npm run build` with 0 errors and 0 HTML nesting warnings.
