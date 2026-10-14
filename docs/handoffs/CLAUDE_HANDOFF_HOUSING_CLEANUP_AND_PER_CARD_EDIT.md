# Architectural Specification: Housing Hub Layout Cleanup & Per-Card Edit Standardization

**Role:** Lead Architect & UX Designer  
**Audience:** Claude Code (Lead Frontend & Backend Implementer)  
**Status:** Approved for Implementation  
**Governing Rules:** Rules U1 through U6 in `docs/ARCHITECTURE.md`  
**Target Files:**
- `frontend/src/pages/housing/HousingHub.tsx`
- `frontend/src/pages/services/RentConfig.tsx`
- `frontend/src/pages/services/Rent.tsx`
- `frontend/src/pages/Setup.tsx`
- Tests: `HousingHub.test.tsx`, `RentConfig.test.tsx`, `Rent.test.tsx`, `Setup.test.tsx`

---

## 1. Objectives

1. **Eliminate the 5 Cramped Top Tiles on `/housing`:**
   - Remove the `HousingSummary` tile strip. No more truncated apartment names (`Supertec...`), cut-off UPI IDs (`kumar.yadav@okhdfcb...`), or clunky blue phone outlines.
   - The page header leads directly into the navigation tabs.
2. **Proper Full-Width Cards with Scoped Independent Editing (Rule U5):**
   - Format each domain properly as its own card with full width, clear key-value typography, and its own scoped edit trigger.
   - Clicking edit on one card only edits that specific card.
3. **Standardize Action Triggers to `<Pencil /> Edit` (Rule U5):**
   - Strip out verbose button labels (`Edit Tenancy Terms`, `Edit Settings`, `Change Agreement Model`).
   - Use a concise, uniform `<Pencil className="w-3.5 h-3.5" /> Edit` button.
4. **Zero Dead Space for Empty History (Rule U6):**
   - Only render the `Completed Terms` table when archived terms actually exist (`terms.length > 0`).

---

## 2. Page-by-Page Specifications

### 2.1. Housing Hub (`frontend/src/pages/housing/HousingHub.tsx`)
1. **Remove `HousingSummary`:**
   - Delete the 5 squished top tiles completely.
   - The page renders:
     ```tsx
     <Page
       title="Housing"
       subtitle="Central configuration for the lease contract, security deposit, physical spaces, stakeholders and documents."
     >
       <div className="tabs" role="tablist" aria-label="Housing">
         ...
       </div>
       <div role="tabpanel" ...>
         ...
       </div>
     </Page>
     ```

---

### 2.2. Tenancy & Master Lease Tab (`frontend/src/pages/services/RentConfig.tsx`)
In `LeaseContractSection`:
1. **Card 1: Apartment Profile:**
   - Display full apartment name and address (from `useApp().apartment`).
   - Header Action: `<button className={BUTTON_SECONDARY}><Pencil className="w-3.5 h-3.5" /> Edit</button>` opening a dialog to update the apartment name/address (`PATCH /api/apartments/{apartment_id}`).
2. **Card 2: Master Tenancy Terms:**
   - Display read-only terms: Monthly Rent, Due Day, Contract Start/End Dates, `Month X of Y` pill, Notice Period, Lock-in Period, Escalation Clause.
   - Header Action: `<button className={BUTTON_SECONDARY}><Pencil className="w-3.5 h-3.5" /> Edit</button>`.
   - Clicking edit swaps the card for the edit form with explicit `Cancel` and `Save Changes` buttons.
3. **Card 3: Landlord Profile & Payout:**
   - Display full Name, Phone, Email, PAN, Bank Account Number, IFSC, and complete UPI ID (with copy button).
   - Header Action: `<button className={BUTTON_SECONDARY}><Pencil className="w-3.5 h-3.5" /> Edit</button>`.
   - Clicking edit opens the Contact Edit Modal for the Owner contact (`role === 'owner'`).
4. **Card 4: Completed Terms History (Rule U6):**
   - Only render this card if `terms.length > 0`. When there are no prior terms, render nothing (no giant empty box).

---

### 2.3. Button Label Standardization across the App
1. In `Rent.tsx` (Rental Agreement Structure card):
   - Replace `"Change Agreement Model"` with `<Pencil className="w-3.5 h-3.5" /> Edit`.
2. In `Setup.tsx` (Settings card):
   - Replace `"Edit Settings"` with `<Pencil className="w-3.5 h-3.5" /> Edit`.
3. In `RentConfig.tsx`:
   - Replace `"Edit Tenancy Terms"` with `<Pencil className="w-3.5 h-3.5" /> Edit`.
   - Replace `"Edit in Stakeholders & Contacts →"` with `<Pencil className="w-3.5 h-3.5" /> Edit`.

---

## 3. Verification Checklist

1. **Visual Verification:**
   - Navigating to `/housing`:
     - Top of the page has NO squished tiles.
     - Tabs are clean and immediately visible.
     - Inside Tenancy tab: Apartment details, Tenancy terms, and Landlord profile are each in their own full-width card with no truncation.
     - Each card has a clean `<Pencil /> Edit` button in its header.
     - Completed Terms is completely hidden when there are no archived terms.
2. **Scoped Editing Check:**
   - Clicking Edit on Apartment edits only Apartment.
   - Clicking Edit on Tenancy terms edits only Tenancy terms.
   - Clicking Edit on Landlord edits only Landlord.
3. **Automated Tests:**
   - Update tests in `HousingHub.test.tsx`, `RentConfig.test.tsx`, `Rent.test.tsx`, and `Setup.test.tsx`.
   - Run vitest (`npm --prefix frontend test -- --run`) and ensure all 229+ tests pass.
   - Run build (`npm --prefix frontend run build`) and ensure clean production build.

