# Architectural Specification: Security Deposit Tab UX Clean-Up & Action Standards

**Role:** Lead Architect & UX Designer  
**Audience:** Claude Code (Lead Frontend Implementer)  
**Status:** Approved for Implementation  
**Governing Rules:** Rule U1 (Read-Only Default), Rule U3 (Header Alignment), Rule U5 (Explicit Actions), Rule U6 (Zero Dead Space), Rule U7 (Summary-First Tables with On-Click Detail Modals), Rule D1 (Field Atomicity)  
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

### 2.2. Enforce Rule U7 & Rule D1 on the "Household Escrow Pool" Slots Table

The `Household deposit slots` table previously stacked 2–4 vertical lines inside almost every cell (`Room 1` + `Slot 1` + `Attached Washroom` + `Balcony` in Column 1; `Sarbananda Taw` + `Primary signatory` in Column 3; `Funded` + `₹50,000.00` in Column 4; `5 Oct 2026` + `Bank transfer · NEFT-A...` in Column 5).

Under **Rule U7 (Summary-First Tables)** and **Rule D1 (Atomicity)**, every row in `Household deposit slots` must be a **single-line summary row** (~44px tall), and clicking a row opens the **`Deposit Slot Details`** modal:

1. **6 Center-Aligned Column Headers (Rule U3):**
   - `Slot / Space` (`w-[16%]`, `text-center`)
   - `Target Deposit` (`w-[16%]`, `text-center`)
   - `Current Occupant` (`w-[22%]`, `text-center`)
   - `Funded Status` (`w-[15%]`, `text-center`)
   - `Paid Date` (`w-[15%]`, `text-center`) — renamed from `Paid Date & Ref` (obeying **Rule D1** & **Rule U7**)
   - `Actions` (`w-[16%]`, `text-center`)

2. **Single-Line Atomic Cells in `Household deposit slots`:**
   - **Column 1 (`Slot / Space`):**
     - Render only `slot.slot_label` (`Room 1`, `Room 2`, `Room 3`) inside a focusable `<button type="button" aria-haspopup="dialog">` so keyboard users can press `Enter`/`Space` to open the slot details modal and focus returns to it on close.
     - Remove the second-line `Slot {slot.slot_number}` subtitle and remove the `SlotRoom` amenity pills from the table cell (both move into the **Deposit Slot Details** modal).
   - **Column 2 (`Target Deposit`):**
     - `<Rupees paise={slot.target_deposit_paise} />` (`text-center font-bold`).
   - **Column 3 (`Current Occupant`):**
     - Single line: `<Avatar name={name} />` + `<span className="font-bold text-[var(--text-primary)] truncate">{name}</span>` (or `Vacant` when unassigned).
     - Remove the second-line `Primary signatory` subtitle from the table cell (moves into the **Deposit Slot Details** modal).
   - **Column 4 (`Funded Status`):**
     - Single line: `<Pill tone={STATUS_TONES[slot.status]}>{STATUS_LABELS[slot.status]}</Pill>`.
     - Remove the second-line rupee amount stacked underneath the pill (moves into the **Deposit Slot Details** modal).
   - **Column 5 (`Paid Date`):**
     - Single line (`tabular text-center text-[var(--text-secondary)]`): `slot.funded_date ? formatDate(slot.funded_date) : '—'`.
     - Remove the second-line payment method / reference / `Direct to landlord` sub-line from the table cell (moves into the **Deposit Slot Details** modal).
   - **Column 6 (`Actions`):**
     - Hold only the primary deposit lifecycle actions:
       - `fund`: `<Plus /> Pay` (when occupied and `outstanding_paise > 0`)
       - `vacate`: `<LogOut /> Vacate` (when occupied and not primary signatory)
       - `assign`: `<UserPlus /> Assign` (when not primary signatory and `funded_amount_paise === 0`)
     - **Move `Change Room` / `Link Room` (`kind: 'room'`) out of the table's `Actions` column and inside the `Deposit Slot Details` modal.**
     - When a slot has no row-level lifecycle action (e.g., the primary signatory's own funded slot), render `'—'` in `text-[var(--text-tertiary)]`.
     - Clicking any action button in the `Actions` cell must call `event.stopPropagation()` so it opens its action dialog (`fund` / `vacate` / `assign`) directly without opening the row's details modal.

### 2.3. On-Click "Deposit Slot Details" Modal (`DepositSlotDetailsModal`)

Clicking any row in `Household deposit slots` (outside the `Actions` buttons) or pressing `Enter`/`Space` on the `Slot / Space` button opens a read-only `<Modal title="Deposit Slot Details" description={`${slot.slot_label} · Slot ${slot.slot_number}`}>`:

1. **Top Summary Banner (`rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3.5 flex flex-wrap items-center justify-between gap-3`):**
   - Left: `<span className="flex flex-wrap items-center gap-2"><Pill tone={STATUS_TONES[slot.status]}>{STATUS_LABELS[slot.status]}</Pill>{slot.is_primary_signatory && <Pill tone="accent">Primary signatory</Pill>}</span>`
   - Right: `<span className="text-xs text-[var(--text-secondary)]">Funded <Rupees paise={slot.funded_amount_paise} className="text-base font-bold tabular text-[var(--text-primary)]" /> of <Rupees paise={slot.target_deposit_paise} className="font-semibold tabular text-[var(--text-primary)]" /></span>`
2. **2-Column Atomic Fact Grid (`<dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-3.5 text-xs">`):**
   - **Slot / Space:** `${slot.slot_label} (Slot ${slot.slot_number})`
   - **Linked Room:** `slot.room_number ?? 'Not linked'`
   - **Private Amenities:** When linked to a room, list `Attached Washroom` / `Balcony` (joined by ` · `, or `'None'`); when not linked, `'—'`.
   - **Current Occupant:** `slot.assigned_display_name ? (slot.is_primary_signatory ? `${slot.assigned_display_name} (Primary signatory)` : slot.assigned_display_name) : 'Vacant'`
   - **Occupied Since:** `slot.assigned_since ? formatDate(slot.assigned_since) : '—'`
   - **Outstanding Balance:** `<Rupees paise={slot.outstanding_paise} />`
   - **Paid Date:** `slot.funded_date ? formatDate(slot.funded_date) : slot.is_primary_signatory ? 'Fronted with master deposit' : 'Not paid yet'`
   - **Paid To:** `slot.is_primary_signatory && slot.funded_amount_paise > 0 ? 'Direct to landlord' : slot.paid_to_display_name ?? (summary.funding_mode === 'pooled_direct' && slot.funded_amount_paise > 0 ? 'Direct to landlord' : '—')`
   - **Payment Method:** `slot.payment_method ? METHOD_LABELS[slot.payment_method] : slot.is_primary_signatory ? 'Master lease deposit' : 'Not recorded'`
   - **Reference / UTR:** `slot.payment_reference ? <span className="font-mono break-all">{slot.payment_reference}</span> : 'Not recorded'`
3. **Modal Footer (`flex items-center justify-between gap-3`):**
   - Left: `<button type="button" className={BUTTON_QUIET} onClick={() => onChangeRoom()}><BedDouble className="w-3.5 h-3.5 shrink-0" aria-hidden="true" /> {slot.room_id ? 'Change Room' : 'Link Room'}</button>` — clicking it transitions to `RoomLinkModal` (`setDialog({ kind: 'room', slot })`).
   - Right: `<button type="button" className={BUTTON_SECONDARY} onClick={onClose}>Close</button>`.

### 2.4. Render "Recent Deposit Activity" with Atomic `From` / `To` Columns & Read-Only Modal (Rules U3, U6, U7 & D1)

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

3. **Top-Level Sibling `<Panel>` Separation (Not Nested Inside `Household Escrow Pool`):**
   - `Recent Deposit Activity` must **not** be rendered inside the `Household Escrow Pool` `<Panel>` (which made it look like a subordinate child box of the pool card).
   - Instead, render `Household Escrow Pool` and `Recent Deposit Activity` as two **sibling top-level `<Panel>` cards** inside a vertical stack (`<div className="stack">`):
     - **Card 1:** `<Panel title="Household Escrow Pool" icon={<Landmark ... />} hint="...">` — contains only the 4 `MiniStat` cards, the balance alert (if any), the `Household deposit slots` table, and `SlotDialog`.
     - **Card 2 (when `history.length > 0`, obeying Rule U6):** `RecentDepositActivity` renders its own top-level `<Panel title="Recent Deposit Activity" icon={<History className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />} hint="Payments, refunds and deductions recorded across the household's deposit slots.">` directly below the `Household Escrow Pool` panel.
   - Inside the `Recent Deposit Activity` `<Panel>`, render the `<div className="overflow-x-auto min-w-0">` and `<table>` directly (without an extra inner `bg-[var(--bg-elevated)]` box wrapper, since `<Panel>` itself provides the card frame).

4. **6-Column Table Structure (No `Details` Column, No Chevrons; All `<th>` Center-Aligned per Rule U3):**
   - `Date` (`w-[14%]`, `text-center`) — plain tabular date (`12 Oct 2026`).
   - `Slot / Space` (`w-[13%]`, `text-center`) — slot label pill (`Room 2`).
   - `Activity` (`w-[21%]`, `text-center`) — `<Pill tone={TX_TONES[tx.tx_type]}>{TX_LABELS[tx.tx_type]}</Pill>`.
   - `From` (`w-[18%]`, `text-center`) — atomic payer name (`truncate` with `title={from}`).
   - `To` (`w-[18%]`, `text-center`) — atomic recipient name (`truncate` with `title={to}`).
   - `Amount` (`w-[16%]`, `text-center`) — bold tabular `<Rupees paise={tx.amount_paise} />`.

5. **Openable Row — Read-Only Transaction Modal (Zero Table Layout Shift):**
   - **State:** `const [selected, setSelected] = useState<{ tx: DepositTransaction; slot: DepositSlot } | null>(null)` in `RecentDepositActivity`.
   - **Trigger:** Clicking any transaction row (`onClick={() => setSelected({ tx, slot })}`, plus keyboard accessibility via the date button) opens a read-only `<Modal>` for that transaction.
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

1. **Household Escrow Pool Table & Slot Details Modal (`SecurityDeposits.test.tsx`):**
   - Assert the 6 centered column headers in `Household deposit slots`: `['Slot / Space', 'Target Deposit', 'Current Occupant', 'Funded Status', 'Paid Date', 'Actions']` (Rule U3).
   - Verify the table cells render single-line summary values only (no `Slot 1` sub-line, no amenity pills, no `UPI · UTR...` sub-line in the table).
   - Verify the `Actions` column contains only lifecycle actions (`Pay`, `Vacate`, `Assign`), and `Link Room` / `Change Room` lives inside the `"Deposit Slot Details"` dialog.
   - Verify clicking a slot row opens `"Deposit Slot Details"` (`role="dialog"`) showing all 10 facts (`Slot / Space`, `Linked Room`, `Private Amenities`, `Current Occupant`, `Occupied Since`, `Outstanding Balance`, `Paid Date`, `Paid To`, `Payment Method`, `Reference / UTR`), and clicking `Link Room` / `Change Room` in its footer opens the room link dialog.
   - Verify clicking an action button in the `Actions` column (e.g. `Vacate`, `Pay`, `Assign`) opens that action dialog directly without opening `"Deposit Slot Details"`.
2. **Recent Deposit Activity Card (`SecurityDeposits.test.tsx`):**
   - Assert that `Household Escrow Pool` and `Recent Deposit Activity` are sibling `<section>` panels (Rule U6).
   - Run vitest: `npm test -- --run` $\rightarrow$ all tests must pass.






