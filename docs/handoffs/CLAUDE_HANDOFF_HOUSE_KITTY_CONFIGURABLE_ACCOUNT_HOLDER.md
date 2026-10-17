# Architectural Specification: House Kitty Configurable Account Holder & Banking Decoupling

**Role:** Lead Architect & Domain Modeler  
**Audience:** Claude Code (Lead Backend & Frontend Implementer)  
**Status:** Approved by User for Implementation  
**Target Migration:** `0018_house_kitty_account_holder.sql`  
**Governing Standards:** Rule D1 (Field Atomicity), Rule U1 (Unified Presentation), and Fixed Domain Principles.  
**Entities Covered:** `apartment` table, `HouseholdPoolCard`, `HouseholdPoolModal`, and Pool Statements.

---

## 1. Domain Context & Mandate

### The Requirement
In real-world shared households, **no bank account is named "House Kitty"**. 
A shared household kitty/pool is physically held in a personal bank account or digital UPI wallet belonging to an actual person (e.g. the primary tenant, a designated roommate, or an operator). When roommates send money via Google Pay or PhonePe, the banking app displays the real legal name of the account holder.

Therefore, we must decouple:
1. **The System Accounting Identity (Constant):**
   - The platform always knows and displays this accounting entity as **House Kitty** (or Household Pool, `__pool__`).
   - In expense forms, transfer selectors, and ledger statements, users select and see **House Kitty** as the payment source or destination.
2. **The Physical Banking Instance (Configurable per Apartment):**
   - The platform must not mandate or hardcode who holds the kitty account.
   - Each apartment configures its own physical banking destination:
     - **Account Holder Name:** The legal name of the person whose bank account or UPI ID holds the kitty cash (e.g. `"Sarbananda Taw"`).
     - **Bank Account Number**
     - **IFSC Code**
     - **UPI ID**

---

## 2. Database Schema Migration (`0018_house_kitty_account_holder.sql`)

Create migration `aem/persistence/postgres/migrations/0018_house_kitty_account_holder.sql`:

```sql
-- Migration 0018: House Kitty Configurable Account Holder
-- Decouples the system pool identity ('House Kitty') from the real human account holder.

ALTER TABLE apartment
    ADD COLUMN pool_account_holder TEXT
        CHECK (pool_account_holder IS NULL OR length(trim(pool_account_holder)) > 0);

-- Normalize existing pool_name to 'House Kitty', and if pool_name was previously
-- customized to a human's name (not 'Household Pool' or 'House Kitty'), preserve it
-- as the pool_account_holder.
UPDATE apartment
SET pool_account_holder = CASE
        WHEN pool_name NOT IN ('Household Pool', 'House Kitty') THEN pool_name
        ELSE NULL
    END,
    pool_name = 'House Kitty';
```

---

## 3. Backend Implementation

### 3.1. Schemas & Models (`aem/schemas/models.py`)
1. **`ApartmentResponse`:**
   - Add `pool_account_holder: str | None = None`
   - `pool_name: str` (defaults to `"House Kitty"`)
2. **`ApartmentUpdate`:**
   - Add `pool_account_holder: str | None = Field(None, max_length=200)`
   - Keep `pool_name: str | None = Field(None, max_length=200)`

### 3.2. Apartment Service (`aem/services/apartments.py`)
1. Update `DEFAULT_POOL_NAME`:
   ```python
   DEFAULT_POOL_NAME = "House Kitty"
   POOL_FIELDS = ("pool_name", "pool_account_holder", "pool_bank_account", "pool_bank_ifsc", "pool_upi_id")
   ```
2. In `update_apartment(...)`:
   - Validate and handle `pool_account_holder`:
     ```python
     if "pool_account_holder" in changes:
         after["pool_account_holder"] = optional_text(
             changes["pool_account_holder"], "pool_account_holder", max_length=200
         )
     ```
   - Ensure `pool_account_holder` is included in repository update call and audit trail.

### 3.3. Persistence Repository (`aem/persistence/repositories/apartments.py`)
- Update `update()` method signature and SQL statement to persist `pool_account_holder`.
- Ensure `SELECT *` from `apartment` automatically includes `pool_account_holder`.

### 3.4. Pool Statements Service (`aem/services/pool_statements.py`)
- In `get_payment_pools_statement(...)`, ensure the returned metadata includes `pool_account_holder`:
  ```python
  "account_holder": apartment.get("pool_account_holder"),
  ```

---

## 4. Frontend UI Implementation

### 4.1. TypeScript Types (`frontend/src/api/types.ts`)
Update `Apartment`:
```ts
export interface Apartment {
  // ... existing fields ...
  pool_name: string
  pool_account_holder?: string | null
  pool_bank_account?: string | null
  pool_bank_ifsc?: string | null
  pool_upi_id?: string | null
}
```

### 4.2. House Kitty Account Card (`frontend/src/pages/Payments.tsx`)
In `HouseholdPoolCard`:
1. The card title remains **`House Kitty Account`**.
2. Replace `<DetailFact label="Account Name">{apartment.pool_name || 'House Kitty'}</DetailFact>` with:
   ```tsx
   <DetailFact label="Account Holder">
     {apartment.pool_account_holder ? (
       <span>{apartment.pool_account_holder}</span>
     ) : (
       notRecorded
     )}
   </DetailFact>
   ```
3. Facts displayed in the card grid:
   - **Account Holder:** `apartment.pool_account_holder || 'Not recorded'`
   - **Bank Account:** `apartment.pool_bank_account || 'Not recorded'`
   - **IFSC:** `apartment.pool_bank_ifsc || 'Not recorded'`
   - **UPI ID:** `apartment.pool_upi_id || 'Not recorded'` (with Copy button)

### 4.3. House Kitty Edit Modal (`frontend/src/pages/Payments.tsx`)
In `HouseholdPoolModal`:
1. **Modal Copy:**
   - `title="House Kitty Account & Banking Details"`
   - `description="The household pool is identified as 'House Kitty' across all accounting. Enter the real bank account and UPI details where kitty funds are held."`
2. **Form Fields:**
   - Field 1: **Account Holder Name** (replaces old "Account Name"):
     - `label="Account Holder Name"`
     - `missing="Enter the account holder name."`
     - `placeholder="e.g. Sarbananda Taw"`
     - `hint="The legal name on the bank account or UPI ID (e.g. your name or the managing roommate's name)."`
     - Value initialized to `apartment.pool_account_holder || ''`.
   - Field 2: **Bank Account** (digits, optional)
   - Field 3: **IFSC** (11-character code, optional)
   - Field 4: **UPI ID** (format, optional)
3. **Payload on Save:**
   ```ts
   api.patch<Apartment>(`/api/apartments/${encodeURIComponent(apartment.apartment_id)}`, {
     pool_name: 'House Kitty',
     pool_account_holder: name.trim() || null,
     pool_bank_account: account,
     pool_bank_ifsc: ifsc,
     pool_upi_id: upi,
   })
   ```

---

## 5. Verification Checklist

1. **Database Migration:**
   - Run `./scripts/manage.py init`.
   - Verify `pool_account_holder` column exists in `apartment`.
2. **Backend Tests:**
   - Run:
     ```bash
     .venv/bin/pytest tests/test_apartments.py tests/test_api.py
     ```
   - Assert `pool_account_holder` updates cleanly via `/api/apartments/{id}`.
3. **Frontend Tests:**
   - Run:
     ```bash
     npm --prefix frontend test -- --run
     ```
   - In `Payments.test.tsx`, update test assertions:
     - Check for `Account Holder` fact instead of `Account Name`.
     - Test editing the House Kitty and setting an account holder (e.g. `"Sarbananda Taw"`).
4. **Live Verification:**
   - Open `/payments` on port 8787.
   - In the **House Kitty Account** card, verify the label says **Account Holder**.
   - Click `Edit house kitty`:
     - Modal title: "House Kitty Account & Banking Details".
     - Enter Account Holder Name (e.g. `"Sarbananda Taw"`), Bank Account, IFSC, and UPI ID.
     - Save: Card displays `Account Holder: Sarbananda Taw`.

