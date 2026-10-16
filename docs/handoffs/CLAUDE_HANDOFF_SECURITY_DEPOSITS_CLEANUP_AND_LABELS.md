# Architectural Specification: Security Deposit Tab UX Clean-Up & Action Standards

**Role:** Lead Architect & UX Designer  
**Audience:** Claude Code (Lead Frontend Implementer)  
**Status:** Approved for Implementation  
**Governing Rules:** Rule U1 (Read-Only Default), Rule U3 (Header Alignment), Rule U5 (Explicit Actions), Rule D1 (Field Atomicity)  
**Target Files:**
- `frontend/src/pages/services/SecurityDeposits.tsx`
- `frontend/src/components/ui.tsx`
- `frontend/src/components/Layout.tsx`
- `frontend/src/pages/services/SecurityDeposits.test.tsx`

---

## 1. Context & Identified Defects

An audit of the **Security Deposit** tab (`/housing?tab=deposits`) in the live running application identified 5 visual, usability, and domain presentation issues:

1. **Truncated Stat Card Titles:** In `EscrowPoolCard`, uppercase stat headers (`MASTER DEPOSIT FRONTED TO LA...`, `TOTAL SLOT REIMBURSEMENTS CO...`, `PRIMARY SIGNATORY NET CAPITAL...`) exceed container width in the 4-column layout and are truncated with an abrupt ellipsis (`...`).
2. **Inconsistent & Cryptic Action Buttons (Rule U5 Violation):**
   - Slots 1 and 2 (Rohan & Arnab) display mystery icon-only square buttons `[ ➔ ]` and `[ 🛏️ ]` with zero text labels.
   - Slot 3 (Sarbananda, Primary Signatory) displays a full text button `[ 🛏️ Change Room ]`.
   - **Root Cause:** `SlotActions` contains logic (`alone = actions.length === 1`) that conditionally hides text (`sr-only`) whenever a slot has multiple actions.
3. **Triple Room Duplication in "Slot / Space" Column:**
   - The cell displays:
     - Title: **Room 1** (`slot.slot_label`)
     - Subtitle: *Slot 1* (`slot.slot_number`)
     - Pill: `[ 🛏️ Room 1 🚿 🌅 ]` (`SlotRoom`)
   - It repeats "Room 1" right beneath "Room 1".
4. **Unformatted & Cluttered "Recent Deposit Activity":**
   - Six duplicate/erratic entries on Room 2 (including ₹0.00 refunds) sit as raw unstyled text at the bottom of the card with zero card framing or visual hierarchy.
5. **Top Ribbon Brand Truncation:**
   - In the master top navigation ribbon, `Flat 1805, CG6, Su...` is truncated.

---

## 2. Specification & Implementation Requirements

### 2.1. Stat Card Header Optimization (`frontend/src/pages/services/SecurityDeposits.tsx` & `ui.tsx`)

1. **Concise Labels:** Update `EscrowPoolCard` metric labels to be scannable and compact:
   - `Master Deposit Fronted to Landlord` $\rightarrow$ **`Master Deposit Fronted`**
   - `Total Slot Reimbursements Collected` $\rightarrow$ **`Reimbursements Collected`**
   - `Primary Signatory Net Capital at Risk` $\rightarrow$ **`Signatory Capital at Risk`**
   - `Vacant / Unfunded Gap` $\rightarrow$ **`Unfunded Gap`**
2. **Tooltip & Wrap Support:** In [`MiniStat`](frontend/src/components/ui.tsx):
   - Add `title={typeof label === 'string' ? label : undefined}` to the label container.
   - Ensure the label does not rigidly truncate when there is vertical room, or keep the concise labels strictly within single-line bounds.

### 2.2. Standardize Action Buttons Across All Rows (`SlotActions`)

Eliminate mystery icons and provide consistent, labeled actions for all tenants:

1. **Remove Conditional Hiding:** Delete `alone ? undefined : index === 0 ? 'sr-only xl:not-sr-only' : 'sr-only'`.
2. **Explicit Action Labels:** Every action button must render an icon AND a concise text label:
   - `fund`: icon `Plus`, label `Record Payment`, text `Pay` (or `Record Payment` if space permits)
   - `vacate`: icon `LogOut`, label `Vacate & Settle`, text `Vacate`
   - `assign`: icon `UserPlus`, label `Assign Occupant` / `Reassign Occupant`, text `Assign`
   - `room`: icon `BedDouble`, label `Change Room` / `Link Room`, text `Room` (or `Change Room`)
3. **Button Sizing:**
   - Style with `inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-semibold whitespace-nowrap rounded-lg`.
   - Maintain `w-[26%]` in the table header to accommodate two labeled action buttons comfortably side-by-side.

### 2.3. De-Duplicate Room Labels in "Slot / Space" Column (`SlotRoom`)

1. In `SlotRoom({ slot })`:
   - Compare `slot.slot_label.trim().toLowerCase()` with `slot.room_number?.trim().toLowerCase()`.
   - **Case 1 (Slot label matches room name, e.g. "Room 1" == "Room 1"):**
     - Do not repeat `slot.room_number` in the pill badge!
     - If the room has private amenities, render only the amenities:
       - `🚿 Attached Washroom`
       - `🌅 Balcony`
       - e.g. `<span className="...">{amenities.join(' · ')}</span>`
     - If the room has NO private amenities, return `null` (do not render an empty redundant pill).
   - **Case 2 (Slot label has a custom name, e.g. "Master Bedroom" or "North Wing"):**
     - Keep the room indicator pill: `[ 🛏️ {slot.room_number} · {amenities} ]`.

### 2.4. Format & Frame "Recent Deposit Activity"

1. **Card Framing:** Wrap the activity section in a dedicated sub-panel:
   ```tsx
   <section aria-label="Recent deposit activity" className="mt-4 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4 space-y-3">
     <h3 className="m-0 text-xs font-bold uppercase tracking-wider text-[var(--text-tertiary)]">
       Recent Deposit Activity
     </h3>
     ...
   </section>
   ```
2. **Activity List Items:**
   - Filter out `0` rupee no-op transactions (`amount_paise > 0`) from the recent summary log.
   - For each transaction, display:
     - Date (`tabular text-xs text-[var(--text-secondary)]`)
     - Slot badge (`Room 1`, `Room 2`)
     - Transaction Type Pill (`Paid in` with soft green/accent styling; `Refunded on leaving` with neutral/amber styling)
     - Tenant Name (`font-medium text-[var(--text-primary)]`)
     - Formatted Amount (`Rupees` bold tabular)

### 2.5. Refine Top Ribbon Apartment Name (`Layout.tsx`)

1. In `frontend/src/components/Layout.tsx`:
   - Increase desktop `max-w` constraint from `lg:max-w-[300px]` to `lg:max-w-[420px]` so standard Indian society addresses don't truncate prematurely.
   - When `society_name` and `flat_number` exist on `apartment`, format cleanly with clear delimiter (e.g. `1805, CG6 · Supertech Capetown`).
   - Retain full text in `title` attribute for tooltip on hover.

---

## 3. Test & Verification Checklist

1. **SecurityDeposits Unit Tests (`frontend/src/pages/services/SecurityDeposits.test.tsx`):**
   - Update lines checking metric ribbon text (e.g., `Master Deposit Fronted`, `Reimbursements Collected`, `Signatory Capital at Risk`, `Unfunded Gap`).
   - Verify all action button assertions pass (e.g. `Vacate & Settle`, `Link Room`, `Change Room`).
   - Run vitest: `npm test -- --run` $\rightarrow$ all tests must pass.
2. **Visual Invariants:**
   - Stat cards in 4-column grid fit cleanly on 1366px, 1280px, and 1024px screens without ellipsis.
   - All rows in the Escrow Pool table display consistently labeled action buttons.
   - Slot 1 no longer says `"Room 1"` twice.
   - Recent Deposit Activity renders inside a clean styled container without ₹0 noise.

