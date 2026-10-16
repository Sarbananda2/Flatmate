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

### 2.4. Render "Recent Deposit Activity" with Atomic `From` / `To` Columns & Inline Row Expansion (Rules U3 & D1)

1. **Problems Identified in the Previous Table Iteration:**
   - **Rule D1 Violation & Truncation in `Resident`:** Combining two names with an arrow (`Anangarag Mahanta → Sarbana...`) inside a single `Resident` column violates **Rule D1 (Strict Field Atomicity)** and truncates long Indian names.
   - **Directional Bug on Refunds & Replacement Transfers:** The backend stores `resident_id` as the slot occupant/leaver and `counterparty_resident_id` as the signatory (or incoming replacement). Rendering `{resident} → {counterparty}` unconditionally caused `Refunded on leaving` to display `Arnab Deka → Sarbananda Taw` (backwards — on a refund, money moves **from** Sarbananda Taw **to** Arnab Deka).
   - **Cramped `Details` Column:** Cramming payment method, UTR reference, and notes into a narrow `13%` column truncated strings (`Sample payment...`, `Bank transfer · N...`).

2. **Atomic `From` and `To` Direction Mapping:**
   Derive the payer (`from`) and recipient (`to`) based on `tx.tx_type`:
   ```ts
   function txParties(
     tx: DepositTransaction,
     summary: HouseholdDepositSummary,
   ): { from: string; to: string } {
     const occupant = tx.resident_display_name ?? '—'
     const holder =
       tx.counterparty_display_name ??
       (summary.funding_mode === 'primary_signatory'
         ? (summary.primary_signatory_name ?? 'Primary signatory')
         : 'Landlord')

     switch (tx.tx_type) {
       case 'funding_payment':
         // Occupant pays deposit into escrow holder (signatory or landlord)
         return { from: occupant, to: holder }
       case 'move_out_refund':
         // Escrow holder refunds the departing occupant
         return { from: holder, to: occupant }
       case 'direct_replacement_transfer':
         // Incoming replacement (counterparty) buys out the departing occupant directly
         return { from: tx.counterparty_display_name ?? '—', to: occupant }
       case 'move_out_deduction':
         // Deduction retained from departing occupant by escrow holder
         return { from: occupant, to: holder }
     }
   }
   ```

3. **6-Column Table Structure (No `Details` Column, No Chevrons; All `<th>` Center-Aligned per Rule U3):**
   - `Date` (`w-[14%]`, `text-center`) — plain tabular date (`12 Oct 2026`).
   - `Slot / Space` (`w-[13%]`, `text-center`) — slot label pill (`Room 2`).
   - `Activity` (`w-[21%]`, `text-center`) — `<Pill tone={TX_TONES[tx.tx_type]}>{TX_LABELS[tx.tx_type]}</Pill>`.
   - `From` (`w-[18%]`, `text-center`) — atomic payer name (`truncate` with `title={from}`).
   - `To` (`w-[18%]`, `text-center`) — atomic recipient name (`truncate` with `title={to}`).
   - `Amount` (`w-[16%]`, `text-center`) — bold tabular `<Rupees paise={tx.amount_paise} />`.

4. **Openable Row — Read-Only Transaction Modal (Zero Table Layout Shift):**
   - **State:** `const [selected, setSelected] = useState<{ tx: DepositTransaction; slot: DepositSlot } | null>(null)` in `RecentDepositActivity`.
   - **Trigger:** Clicking any transaction row (`onClick={() => setSelected({ tx, slot })}`, plus keyboard accessibility via an interactive button or `tabIndex={0}` + `Enter`/`Space` handler) opens a read-only `<Modal>` for that transaction. Do not expand rows inline in the table.
   - **Modal Layout (`title="Deposit Transaction Details"`):**
     - **Summary Banner:** A highlighted bar (`rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3.5 flex items-center justify-between gap-3`) showing `<Pill tone={TX_TONES[tx.tx_type]}>{TX_LABELS[tx.tx_type]}</Pill>` on the left and `<Rupees paise={tx.amount_paise} className="text-base font-bold tabular text-[var(--text-primary)]" />` on the right.
     - **2-Column Atomic Fact Grid (`<dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-3.5 text-xs">`):**
       1. **Date:** `{formatDate(tx.tx_date)}`
       2. **Slot / Space:** `{slot.slot_label} (Slot {slot.slot_number})`
       3. **From:** `{from}`
       4. **To:** `{to}`
       5. **Payment Method:** `{txMethod(tx)}` (`UPI`, `Bank transfer`, `Retained from deposit`, or `Not recorded`)
       6. **Reference / UTR:** `{tx.reference ? <span className="font-mono break-all">{tx.reference}</span> : 'Not recorded'}`
       7. **Notes / Reason (`sm:col-span-2`):** Full untruncated `{tx.notes || 'None'}`.
     - **Footer:** Right-aligned `<button type="button" className={BUTTON_SECONDARY} onClick={onClose}>Close</button>`.

### 2.5. Refine Top Ribbon Apartment Name (`Layout.tsx`)

1. In `frontend/src/components/Layout.tsx`:
   - Increase desktop `max-w` constraint from `lg:max-w-[300px]` to `lg:max-w-[420px]` so standard Indian society addresses don't truncate prematurely.
   - When `society_name` and `flat_number` exist on `apartment`, format cleanly with clear delimiter (e.g. `1805, CG6 · Supertech Capetown`).
   - Retain full text in `title` attribute for tooltip on hover.

---

## 3. Test & Verification Checklist

1. **SecurityDeposits Unit Tests (`frontend/src/pages/services/SecurityDeposits.test.tsx`):**
   - Assert the 6 centered column headers in `Recent deposit activity`: `['Date', 'Slot / Space', 'Activity', 'From', 'To', 'Amount']`.
   - Verify directional accuracy in `From` and `To` across `funding_payment`, `move_out_refund`, and `direct_replacement_transfer`.
   - Verify clicking a row opens the `"Deposit Transaction Details"` dialog (`role="dialog"`) showing `Date`, `Slot / Space`, `From`, `To`, `Payment Method`, `Reference / UTR`, and full `Notes / Reason`, and clicking `Close` dismisses the dialog without shifting the table rows.
   - Run vitest: `npm test -- --run` $\rightarrow$ all tests must pass with zero DOM nesting warnings.




