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

### 4.1 Payments & Credits Page (`frontend/src/pages/Payments.tsx`)
1. **Household Pool Account Card (Read-Only by Default + Edit Modal per Rules U1, U2, U4, U5):**
   - Load apartment details (via `useApp().apartment` and `useApp().reloadApartment`, or `useResource<Apartment>`).
   - Above the tabs on `/payments`, render a clean `Panel` (or `Card`) titled **"Household Pool Account"** with hint *"Where residents send pool payments, rent shares, and settlement transfers."* and an `EditButton` (`label="Edit household pool"`).
   - Display 4 read-only facts in a responsive grid:
     - **Pool Name**: `apartment.pool_name || 'Household Pool'`
     - **Bank Account**: `apartment.pool_bank_account` (tabular) or `'Not recorded'`
     - **IFSC**: `apartment.pool_bank_ifsc` (tabular) or `'Not recorded'`
     - **UPI ID**: `apartment.pool_upi_id` (with `CopyButton`) or `'Not recorded'`
   - Clicking **Edit** opens an `EditHouseholdPoolModal` (`PATCH /api/apartments/${apartment.apartment_id}`) with client-side validation matching `PropertyContacts.tsx`:
     - `Pool Name` (required, max 200 chars)
     - `Bank Account` (optional, 9–18 digits: `/^[0-9]{9,18}$/`)
     - `IFSC` (optional, auto-uppercased, `/^[A-Z]{4}0[A-Z0-9]{6}$/`)
     - `UPI ID` (optional, `/^[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}$/`)
2. **Payment Destination Box in `PaymentDialog` (`Record a payment` on `/payments`):**
   - Also fetch the active lease config (`useResource<{ config: RentLeaseConfig | null }>('/api/rent/config')`) inside `PaymentDialog`.
   - **When `kind === 'to_pool'`:**
     - Show a **"Transfer to: {pool_name}"** destination box (styled like `PayoutModal`'s destination box in `Rent.tsx`) showing the Household Pool's Bank Account, IFSC, and copyable UPI ID (or a helpful note if none are on file yet).
     - Default the `Counterparty` field placeholder to `apartment?.pool_name ?? 'Household Pool'`.
   - **When `kind === 'provider_advance'`:**
     - If `lease.data?.config` exists, offer a quick counterparty selector or pill/option for **Landlord (`{config.landlord_name}`)** vs **Other Provider**. When **Landlord** is selected, auto-fill `Provider paid` with `config.landlord_name` and display the **"Transfer to: {config.landlord_name}"** destination box with the Landlord's Bank Account, IFSC, and UPI ID!

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
   - Test `PATCH /api/apartments/{id}` with `pool_name`, `pool_bank_account`, `pool_bank_ifsc`, `pool_upi_id` (valid and invalid formats).
   - Test `POST /api/rent/deposits/slots/{id}/fund` in `direct_co_tenants` mode with `paid_to_party="landlord"` and `paid_to_party="pool"`, verifying `paid_to_display_name` and transaction `counterparty_display_name` resolve to `Landlord (...)` and `Household Pool` respectively.
   - Test `whatsapp_summary` in `sharing.py` includes `upi_id` and `upi_uri` on transfers to `__pool__` when `pool_upi_id` is configured on the apartment.
   - Run the full backend test suite (`pytest`) and ensure 0 regressions.
2. **Frontend (`npm test` & `npm run build`):**
   - Add/update tests in `SecurityDeposits.test.tsx`, `Payments` tests, and `Rent.test.tsx` covering:
     - Editing the `Household Pool Account` card on `/payments`.
     - Selecting `Paid to: Landlord` vs `Paid to: Household Pool` in `Record Deposit Payment` under `direct_co_tenants` mode, and verifying the destination bank/UPI details update and `"Direct to landlord"` is no longer in the `Payment method` dropdown.
     - Showing the Household Pool destination box in `Record Rent Payment`.
   - Run `npm test` and `npm run build` with 0 errors and 0 HTML nesting warnings.

