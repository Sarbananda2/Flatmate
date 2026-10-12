# Pillar B: Household Security Deposit Slots & Sub-Escrow Architecture
## Architectural Specification & Technical Handoff for Claude Code

**Role:** Lead Architect & Financial Systems Designer  
**Audience:** Claude Code (Lead Backend & Frontend Implementer)  
**Status:** Approved for Implementation  
**Migration Target:** `0008_household_security_deposits.sql`

---

## 1. Domain Philosophy: Household Property Ownership

A security deposit is fundamentally a legal condition of the **Household & Master Lease Premises**, not a personal asset of a transient individual.

```
┌────────────────────────────────────────────────────────────────────────┐
│                      HOUSEHOLD & MASTER LEASE                          │
│               Apartment 402 · Total Deposit: ₹1,50,000                 │
│         Funding Mode: Primary Signatory Fronted (Mandatory Choice)     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Partitions into
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   HOUSEHOLD DEPOSIT ALLOCATION SLOTS                   │
│   (Permanent household property assets; outlive individual flatmates)  │
├────────────────────────────────────────────────────────────────────────┤
│ Slot 1 (Room 1): ₹50,000 target  ──▶ Assigned to: Alice (Signatory)    │
│ Slot 2 (Room 2): ₹50,000 target  ──▶ Assigned to: Bob   (Funded)       │
│ Slot 3 (Room 3): ₹50,000 target  ──▶ Assigned to: Charlie (Funded)     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼ Mid-Lease Turnover (Vacate Slot)
┌────────────────────────────────────────────────────────────────────────┐
│ Bob moves out: Slot 2 remains, marked 'vacating'                       │
│ 1. Deductions assessed (₹5,000 painting)                               │
│ 2. Bob refunded ₹45,000 (by Signatory Alice OR incoming David)         │
│ 3. David moves in ──▶ Assigned to Slot 2 ──▶ Funds ₹50,000             │
└────────────────────────────────────────────────────────────────────────┘
```

### Architectural Benefits:
1. **Permanent Household Continuity:** When Bob vacates, the household doesn't lose its deposit structure. Slot 2 persists as a household property asset.
2. **Vacant Room Transparency:** If a room sits empty between flatmates, the slot is marked `vacant`, clearly surfacing the household's unfunded capital gap.
3. **Seamless Transition to Next Phase (DEBT-006):** In the upcoming Household Property phase (Rooms & Beds), `room_id` will simply attach directly to `household_deposit_slot` with zero database restructuring!

---

## 2. Invariants & Mathematical Guarantees

* **Invariant B0 (Mandatory Funding Mode — No Defaults):** When setting up the household deposit, the operator must explicitly choose between:
  - `primary_signatory` (Anchor tenant fronted the landlord; flatmates reimburse the anchor)
  - `direct_co_tenants` (All flatmates contribute directly to the landlord or pooled account)
* **Invariant B1 (Escrow Segregation):** Deposit slots belong to the capital escrow ledger. They **never** enter monthly operating statements, expense instances, or monthly net positions.
* **Invariant B2 (Conservation of Household Capital):**
  $$\sum \text{target\_deposit\_paise} = \text{rent\_lease\_config.security\_deposit\_paise}$$
  (Proposes equal split by default across slots, e.g. $1,50,000 / 3 = 50,000$, with custom per-room overrides allowed).
* **Invariant B3 (Slot Turnover Integrity):**
  When a flatmate leaves a slot:
  $$\text{refund\_amount\_paise} + \text{deductions\_paise} = \text{funded\_amount\_paise}$$
  Deductions cannot exceed the funded deposit.
* **Invariant B4 (Dual Turnover Clearance Paths):**
  - **Path A (Signatory Intermediary):** Signatory refunds outgoing flatmate from personal capital, holding the vacant slot until a replacement is assigned.
  - **Path B (Direct Flatmate-to-Flatmate Transfer):** Incoming flatmate pays the outgoing flatmate directly, seamlessly taking over the assigned slot.

---

## 3. Database Schema Migration (`0008_household_security_deposits.sql`)

Create migration `aem/persistence/postgres/migrations/0008_household_security_deposits.sql`:

```sql
-- Migration 0008: Household Security Deposit Slots & Sub-Escrow (Pillar B)

-- 1. Extend rent_lease_config with mandatory deposit funding mode and primary signatory
ALTER TABLE rent_lease_config
    ADD COLUMN deposit_funding_mode TEXT
        CHECK (deposit_funding_mode IS NULL OR deposit_funding_mode IN ('primary_signatory', 'direct_co_tenants')),
    ADD COLUMN deposit_primary_signatory_resident_id TEXT
        REFERENCES resident(resident_id) DEFERRABLE;

-- 2. Household Deposit Slots: Physical property allocations belonging to the Household
CREATE TABLE household_deposit_slot (
    slot_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id) DEFERRABLE,
    config_id TEXT REFERENCES rent_lease_config(config_id) DEFERRABLE,
    slot_number INTEGER NOT NULL CHECK (slot_number > 0),
    slot_label TEXT NOT NULL,
    target_deposit_paise BIGINT NOT NULL CHECK (target_deposit_paise > 0),
    
    -- Current assigned occupant (nullable when vacant)
    assigned_resident_id TEXT REFERENCES resident(resident_id) DEFERRABLE,
    assigned_since TEXT CHECK (assigned_since IS NULL OR assigned_since ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    
    -- Status of this household slot
    status TEXT NOT NULL DEFAULT 'vacant'
        CHECK (status IN ('vacant', 'occupied_pending', 'occupied_funded', 'vacating')),
    
    -- Current occupant's funding status
    funded_amount_paise BIGINT NOT NULL DEFAULT 0 CHECK (funded_amount_paise >= 0),
    funded_date TEXT CHECK (funded_date IS NULL OR funded_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    payment_method TEXT CHECK (payment_method IS NULL OR payment_method IN ('upi', 'bank_transfer', 'cash', 'direct_to_landlord')),
    payment_reference TEXT,
    paid_to_resident_id TEXT REFERENCES resident(resident_id) DEFERRABLE,
    
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_seq BIGINT GENERATED BY DEFAULT AS IDENTITY,
    UNIQUE (apartment_id, slot_number)
);

-- 3. Deposit Slot Transaction Trail (Audit of payments, move-out deductions, refunds & transfers)
CREATE TABLE household_deposit_transaction (
    tx_id TEXT PRIMARY KEY,
    slot_id TEXT NOT NULL REFERENCES household_deposit_slot(slot_id) DEFERRABLE,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id) DEFERRABLE,
    resident_id TEXT NOT NULL REFERENCES resident(resident_id) DEFERRABLE,
    tx_type TEXT NOT NULL
        CHECK (tx_type IN ('funding_payment', 'move_out_deduction', 'move_out_refund', 'direct_replacement_transfer')),
    amount_paise BIGINT NOT NULL CHECK (amount_paise >= 0),
    tx_date TEXT NOT NULL CHECK (tx_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
    counterparty_resident_id TEXT REFERENCES resident(resident_id) DEFERRABLE,
    payment_method TEXT,
    reference TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    row_seq BIGINT GENERATED BY DEFAULT AS IDENTITY
);

CREATE INDEX idx_deposit_slot_apartment ON household_deposit_slot(apartment_id);
CREATE INDEX idx_deposit_slot_resident ON household_deposit_slot(assigned_resident_id);
CREATE INDEX idx_deposit_tx_slot ON household_deposit_transaction(slot_id);
CREATE INDEX idx_deposit_tx_resident ON household_deposit_transaction(resident_id);
```

---

## 4. Backend Implementation

### 4.1 Schemas (`aem/schemas/models.py`)
- `DepositConfigUpdate`: `funding_mode: Literal['primary_signatory', 'direct_co_tenants']`, `primary_signatory_resident_id: str | None`
- `DepositSlotInitRequest`:
  - `slots: list[DepositSlotItem]` (each containing `slot_number`, `slot_label`, `target_deposit_paise`, `assigned_resident_id`)
- `DepositSlotFundingRequest`: `amount_paise`, `paid_date`, `payment_method`, `payment_reference`, `paid_to_resident_id`
- `DepositSlotVacateRequest`:
  - `settlement_path: Literal['signatory_refund', 'direct_transfer']`
  - `deductions_paise: int`
  - `deduction_reason: str | None`
  - `refund_amount_paise: int`
  - `refund_date: str`
  - `refund_reference: str | None`
  - `incoming_resident_id: str | None` (required for `direct_transfer`)
- `HouseholdDepositSummary`:
  - `master_deposit_paise`: int
  - `funding_mode`: `'primary_signatory' | 'direct_co_tenants' | None`
  - `primary_signatory_id`: str | None
  - `primary_signatory_name`: str | None
  - `signatory_capital_exposure_paise`: int (Master Deposit minus total reimbursements received)
  - `total_slots_count`: int
  - `vacant_slots_count`: int
  - `total_funded_paise`: int
  - `slots`: list of slot models with resident details and transaction history

### 4.2 Repositories & Services (`aem/persistence/repositories/deposits.py` & `aem/services/deposits.py`)
- `configure_mode(uow, apartment_id, mode, signatory_id)`
- `initialize_or_update_slots(uow, apartment_id, slot_definitions)`
- `record_slot_funding(uow, slot_id, amount_paise, date, method, ref, paid_to_id)`
- `vacate_and_settle_slot(uow, slot_id, vacate_req)`
- Mutating operations write to `uow.audit` with `entity_type="household_deposit_slot"`.

### 4.3 Router Endpoints (`aem/api/routers/deposits.py`)
- `GET /api/rent/deposits` $\rightarrow$ `HouseholdDepositSummary`
- `POST /api/rent/deposits/config` $\rightarrow$ Configure mode & signatory
- `POST /api/rent/deposits/slots` $\rightarrow$ Initialize / update household slots
- `POST /api/rent/deposits/slots/{slot_id}/fund` $\rightarrow$ Record payment for slot
- `POST /api/rent/deposits/slots/{slot_id}/vacate` $\rightarrow$ Clear & vacate slot (dual path)

---

## 5. Frontend UI/UX Design

### 5.1 Mandatory Deposit Mode Configuration
In `frontend/src/pages/services/Rent.tsx`:
- When unconfigured, renders the mandatory setup modal/card (no pre-selected default):
  1. **Primary Signatory Fronted (Master Tenant):** Requires choosing the Primary Signatory from active residents.
  2. **Direct Co-Tenants:** Joint escrow direct to landlord.
- Automatically generates proposed **Household Deposit Slots** (equal division of master deposit by resident count), allowing custom slot labels (e.g. "Room 1", "Room 2") and custom amounts.

### 5.2 Household Escrow Pool View (`/services/rent`)
- **Capital Exposure Ribbon (for Mode A):**
  - Master Deposit Fronted to Landlord: ₹1,50,000
  - Total Slot Reimbursements Collected: ₹1,00,000
  - Primary Signatory Net Capital at Risk: **₹50,000**
  - Vacant / Unfunded Gap: ₹0
- **Household Slots Directory Table:**
  - **MANDATORY DESIGN RULE:** All table headers (`<th>`) must be **center-aligned (`text-center`) across all columns**.
  - Columns: `Slot / Space` | `Target Deposit` | `Current Occupant` | `Funded Status` | `Paid Date & Ref` | `Actions`
  - Actions: `Record Payment`, `Vacate & Settle`, `Reassign Occupant`

### 5.3 Resident Detail Page (`/residents/:id`)
- A **"Household Deposit Allocation"** card:
  - "Assigned to Household Slot 2 (Room 2)"
  - Target Deposit: ₹50,000 | Funded: ₹50,000
  - Paid to: Alice via UPI (UTR ...)
  - Actions: `Vacate & Settle Refund`

### 5.4 Form Validation Rules (Mandatory System-wide)
- Every input field validated client-side with inline messages and danger borders.
- Deduction limits guarded: $\text{Deductions} \le \text{Funded Amount}$.
- Mandatory deduction reasons when deductions $> 0$.

---

## 6. Verification Commands

```bash
# Backend test suite
.venv/bin/pytest tests/test_deposits.py

# Frontend test suite and build
npm --prefix frontend test
npm --prefix frontend run build
```
