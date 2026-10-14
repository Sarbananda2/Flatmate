# Architectural Specification: Rent vs. Housing Domain Realignment

**Role:** Lead Architect & Financial Systems Designer  
**Audience:** Claude Code (Lead Frontend & Backend Implementer)  
**Status:** Approved for Implementation  
**Target Files:**
- `frontend/src/pages/services/RentConfig.tsx`
- `frontend/src/pages/services/Rent.tsx`
- `frontend/src/pages/housing/HousingHub.tsx`
- Associated tests in `frontend/src/pages/`

---

## 1. Domain Context & Objective

The user identified a key domain boundary:
1. **Rental Agreement Structure (The Split Model):**
   - Single Master Lease (Equal Split) vs Room-Tiered Fixed Pricing vs Square-Footage Ratio.
   - This defines **how rent is shared and billed among flatmates**.
   - It is part of **Rent** (`/services/rent`), **NOT** part of Housing (`/housing`).
2. **Master Contract, Term & Exit Protections, Renewal & Escalation Clause:**
   - Legal tenancy duration (start date, duration, end date, lock-in, notice period).
   - Tenancy renewal and escalation clause (annual increment rate, projected renewal rent, completed terms history, notice to vacate).
   - This legal tenancy framework belongs to **both Housing and Rent**, backed by a **single source of truth** (`rent_lease_config` / `/api/rent/config`).

---

## 2. Architectural Adjustments

### 2.1. `frontend/src/pages/services/RentConfig.tsx`
1. **Extract `RentalAgreementStructurePanel`:**
   - Decouple the `Rental Agreement Structure` panel (which displays the `RENTAL_MODELS` cards) from the general `LeaseForm`.
   - Export `RentalAgreementStructurePanel` so it can be mounted where rent allocation is managed.
2. **`LeaseContractSection` (Used in Housing):**
   - Does **NOT** render the `Rental Agreement Structure` panel.
   - Renders:
     - Master Contract (Monthly Rent, Due Day)
     - Contract Term & Exit Protections (Start Date, Duration, Notice Period, Lock-in Period)
     - Renewal & Escalation Clause (Projected rent, escalation %, completed terms, notice to vacate)
     - Landlord Profile & Payout Details (synced with Owner contact)
     - Receipt Preferences
3. **Keep Single Source of Truth:**
   - Both screens read and write to the existing `/api/rent/config` endpoints. No schema or database changes needed.

### 2.2. `frontend/src/pages/housing/HousingHub.tsx`
1. Update tab label in `HOUSING_TABS`:
   - Change label from `'Lease & Contract'` to `'Tenancy & Master Lease'` (value remains `'lease'`).
2. On `/housing?tab=lease`, users manage the dwelling's master contract and tenancy protections without seeing internal flatmate split models.

### 2.3. `frontend/src/pages/services/Rent.tsx`
1. **Expose Rental Agreement Structure on the Rent Screen:**
   - Add a section or modal button: **"Rental Agreement Structure"** (e.g. adjacent to the split mode selector or in an expandable panel).
   - Render `RentalAgreementStructurePanel` showing the active model (Single Master Lease Equal Split) and available tiers (Room-Tiered, SqFt).
2. **Bridge to Shared Contract Terms:**
   - Maintain the link/button to `/housing?tab=lease` labelled **"Master Tenancy & Renewal Terms →"** so flatmates can view or edit the shared contract lifecycle.

---

## 3. Verification & Acceptance Criteria

1. **Clean Visual Separation:**
   - On `/housing?tab=lease`: Confirm the "Rental Agreement Structure" model selection cards are **not** displayed. The tab cleanly focuses on contract dates, lock-in/notice periods, renewal escalation, and landlord details.
   - On `/services/rent`: Confirm the "Rental Agreement Structure" is accessible where rent cycles and flatmate shares are managed.
2. **Single Source of Truth Check:**
   - Changing lease dates, monthly rent, or notice periods in either screen persists to `/api/rent/config` and immediately reflects on both screens.
3. **Automated Tests:**
   - Update tests in `RentConfig.test.tsx`, `HousingHub.test.tsx`, and `Rent.test.tsx`.
   - Run vitest (`npm --prefix frontend test -- --run`) and verify all 217+ tests pass.

