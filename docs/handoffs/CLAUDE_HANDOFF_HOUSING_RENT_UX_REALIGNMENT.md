# Architectural Specification: Clean Separation of Housing (Assets) vs. Rent (Monthly Operations)

**Role:** Lead Architect & Financial Systems Designer  
**Audience:** Claude Code (Lead Frontend & Backend Implementer)  
**Status:** Approved for Implementation  
**Key Target Files:**
- `frontend/src/pages/services/RentConfig.tsx` (Declutter into Tenancy Contract view)
- `frontend/src/pages/services/Rent.tsx` (Integrate Rental Agreement Structure & Receipt Preferences)
- `frontend/src/pages/services/ServicesHub.tsx` (Remove circular Housing card)
- `frontend/src/pages/housing/HousingHub.tsx` (Refine Tenancy tab & eliminate duplicates)
- Tests: `RentConfig.test.tsx`, `HousingHub.test.tsx`, `Rent.test.tsx`, `ServicesHub.test.tsx`

---

## 1. The Core Problem & Architectural Goal

Previously, when the `/housing` page was created, the entire monolithic `RentConfig` page was dumped into Housing Tab 1. This caused:
1. **Financial Split Models inside Physical Housing:** Flatmate split strategies (*Equal Split, Room-Tiered, Sq.Ft*) were placed in Housing, where they do not belong.
2. **Massive Data Duplication:** Landlord Bank Account, IFSC, PAN, and UPI appeared in Housing Tab 1 *and* Housing Tab 4 (Owner Contact), and Security Deposit appeared in Tab 1 *and* Tab 2.
3. **Circular Navigation:** `/services` had a "Housing Configuration" card that bounced back to `/housing`, while `/housing` had an "Open monthly rent" button that bounced back to `/services/rent`.

This specification restores a clean, intuitive mental model:
- **Housing (`/housing`)** = The **Home** (Dwelling, Tenancy Contract, Deposit Escrow, Rooms, Stakeholders, Document Vault).
- **Services & Bills (`/services`)** = **Monthly Execution** (Monthly Rent cycle, Electricity bill calculations, future utilities).

---

## 2. Surgical Changes by Page

### 2.1. Housing Page (`/housing` & `HousingHub.tsx`)

#### Tab 1: "Tenancy & Master Lease" (`?tab=lease`)
Refactor the view rendered in Tab 1 (in `RentConfig.tsx` / `HousingHub.tsx`):
1. **STRICTLY RETAIN (The Tenancy Lifecycle):**
   - **Master Tenancy Contract:** Start Date, Duration (e.g. 11 months), End Date, Term Progress (Month X of Y), Monthly Base Rent (₹28,500), Due Day of Month (5th).
   - **Tenancy Protections:** Notice Period Days (30 days), Lock-in Months (6 months).
   - **Renewal & Escalation Clause:** Escalation Type (`percent` / `fixed`), Rate (5%), Projected Renewal Rent (₹29,925), Actions (Renew Lease, Record Notice to Vacate), and Completed Terms History table.
   - **Landlord Reference:** Render a read-only card of the Landlord (Kumar Yadav, phone, UPI) with a quick shortcut: *"Edit in Stakeholders & Contacts →"* (`?tab=contacts`).
2. **STRICTLY REMOVE FROM HOUSING:**
   - ❌ **Remove "Rental Agreement Structure"** panel (Equal Split, Room-Tiered, etc.). This belongs exclusively to Rent.
   - ❌ **Remove "Receipt Preferences"** panel (*Requires Landlord Receipt*). This belongs to Rent.
   - ❌ **Remove duplicate Security Deposit input.** (Deposit escrow is fully managed in Tab 2).
   - ❌ **Remove duplicate Bank Account / IFSC / PAN edit form.** (The Owner in Tab 4 is the single place to manage the owner's bank details).

---

### 2.2. Services & Utilities Page (`/services` & `ServicesHub.tsx`)

1. **Remove Circular Housing Card:**
   - Delete the redundant `Housing Configuration` card from `ServicesHub.tsx`.
   - The user already has **Housing** as a prominent top-level tab in the ribbon. A card on `/services` pointing back to `/housing` creates circular navigation.
2. **Focus 100% on Monthly Recurring Bills:**
   - **Rent** (`/services/rent`): Monthly rent collection & payout.
   - **Electricity** (`/services/electricity`): Smart meter / bill calculation.
   - **Planned Utilities**: Broadband, Maid/Cook, Cooking Gas, Water.

---

### 2.3. Rent Page (`/services/rent` & `Rent.tsx`)

1. **Integrate "Rental Agreement Structure":**
   - On `/services/rent`, flatmates manage how rent is split:
     - Render the **Rental Agreement Structure** section / modal:
       - Displays active model: **Single Master Lease (Equal Split)** (₹9,500/resident).
       - When room-tiered is configured: allows toggling **Room-Tiered Fixed Pricing**.
   - Render the **Receipt Preferences** toggle (*"Requires Landlord Rent Receipts"* for HRA slips).
2. **Master Tenancy Bridge:**
   - Keep a clean, compact **"Master Tenancy & Contract Term"** badge or card:
     - Displays: `1 Apr 2026 – 28 Feb 2027 (Month 7 of 11) · Landlord: Kumar Yadav · 30-day notice`.
     - Button: **"Manage Master Tenancy & Renewal →"** linking to `/housing?tab=lease`.

---

## 3. Data Integrity & Single Source of Truth

* **Zero Schema Changes:** The underlying backend models (`rent_lease_config`, `lease_term`, `household_contact`) remain untouched.
* Any edits to Master Contract dates, monthly rent, or notice periods from either screen update the same `/api/rent/config` endpoint and reflect immediately everywhere.

---

## 4. Acceptance Criteria & Verification

1. **Housing Tab 1 (`/housing?tab=lease`):**
   - Shows only the Master Tenancy Contract term, exit protections, escalation clause, and renewal history.
   - Does **not** contain split models, receipt toggles, or duplicate bank forms.
2. **Services Hub (`/services`):**
   - Shows only operational bills (Rent, Electricity, planned services). No circular "Housing" card.
3. **Rent Page (`/services/rent`):**
   - Displays the Rental Agreement Structure and Receipt Preferences directly on the Rent screen.
4. **Automated Tests:**
   - Run vitest (`npm --prefix frontend test -- --run`).
   - Confirm all 217+ tests pass and build succeeds with zero errors.

