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

### 2.2. Resident-First "Household Escrow Pool" Table (Rules U3, U7 & D1)

Financially, a security deposit in escrow belongs to a **person (Resident)** — people pay deposits, change rooms, and vacate — while the **Room** is the physical space they occupy (or the placeholder when a room is temporarily vacant).

Make **`Resident`** the primary subject (Column 1, `<th scope="row">`) and **`Room`** Column 2, eliminating the redundant "Slot vs. Room" duplication:

1. **6 Center-Aligned Column Headers (Rule U3):**
   - `Resident` (`w-[22%]`, `text-center`)
   - `Room` (`w-[16%]`, `text-center`)
   - `Target Deposit` (`w-[16%]`, `text-center`)
   - `Funded Status` (`w-[15%]`, `text-center`)
   - `Paid Date` (`w-[15%]`, `text-center`)
   - `Actions` (`w-[16%]`, `text-center`)

2. **Single-Line Atomic Cells in `Household deposit slots`:**
   - **Column 1 (`Resident`, `<th scope="row">`):**
     - Render a focusable `<button type="button" aria-haspopup="dialog">` inside `<th scope="row">` so keyboard users can press `Enter`/`Space` on the resident to open the details dialog and focus returns there on close.
     - When occupied: `<Avatar name={name} size="sm" />` + `<span className="font-bold text-[var(--text-primary)] truncate">{name}</span>`.
     - When vacant: `<span className="font-semibold text-[var(--text-tertiary)]">Vacant</span>` inside the button.
   - **Column 2 (`Room`):**
     - Single line (`text-center font-semibold text-[var(--text-secondary)]`): `slot.room_number ?? slot.slot_label` (e.g. `Room 1`, `Room 2`, `Room 3`).
   - **Column 3 (`Target Deposit`):**
     - `<Rupees paise={slot.target_deposit_paise} />` (`text-center font-bold`).
   - **Column 4 (`Funded Status`):**
     - Single line: `<Pill tone={STATUS_TONES[slot.status]}>{STATUS_LABELS[slot.status]}</Pill>`.
   - **Column 5 (`Paid Date`):**
     - Single line (`tabular text-center text-[var(--text-secondary)]`): `slot.funded_date ? formatDate(slot.funded_date) : '—'`.
   - **Column 6 (`Actions`):**
     - Only lifecycle actions (`Pay`, `Vacate`, `Assign`), or `'—'` when none apply (such as the primary signatory's funded share).
     - `Change Room` / `Link Room` lives inside the on-click details dialog.
     - Action button clicks call `event.stopPropagation()` so they open their own dialog without triggering the row click.

### 2.3. On-Click "Deposit Details" Modal (No Redundant `Slot 1` vs `Room 1` Duplication)

Clicking any row in the `Household Escrow Pool` table opens a read-only `<Modal title="Deposit Details" description={`${slot.assigned_display_name ?? 'Vacant'} · ${slot.room_number ?? slot.slot_label}`}>`:

1. **Top Summary Banner (`rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3.5 flex flex-wrap items-center justify-between gap-3`):**
   - Left: `<span className="flex flex-wrap items-center gap-2"><Pill tone={STATUS_TONES[slot.status]}>{STATUS_LABELS[slot.status]}</Pill>{slot.is_primary_signatory && <Pill tone="accent">Primary signatory</Pill>}</span>`
   - Right: `<span className="text-xs text-[var(--text-secondary)]">Funded <Rupees paise={slot.funded_amount_paise} className="text-base font-bold tabular text-[var(--text-primary)]" /> of <Rupees paise={slot.target_deposit_paise} className="font-semibold tabular text-[var(--text-primary)]" /></span>`
2. **2-Column Atomic Fact Grid (`<dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-3.5 text-xs">`):**
   - **Resident:** `slot.assigned_display_name ? (slot.is_primary_signatory ? `${slot.assigned_display_name} (Primary signatory)` : slot.assigned_display_name) : 'Vacant'`
   - **Occupied Since:** `slot.assigned_since ? formatDate(slot.assigned_since) : '—'`
   - **Room:** `slot.room_number ?? slot.slot_label`
   - **Private Amenities:** When linked to a room (`slot.room_id`), list `Attached Washroom` / `Balcony` (joined by ` · `, or `'None'`); when not linked to an inventory room, `'Not linked'`.
   - **Outstanding Balance:** `<Rupees paise={slot.outstanding_paise} />`
   - **Paid Date:** `slot.funded_date ? formatDate(slot.funded_date) : slot.is_primary_signatory ? 'Fronted with master deposit' : 'Not paid yet'`
   - **Paid To:** `slot.is_primary_signatory && slot.funded_amount_paise > 0 ? 'Direct to landlord' : slot.paid_to_display_name ?? (slot.funded_amount_paise > 0 ? 'Direct to landlord' : '—')`
   - **Payment Method:** `slot.payment_method ? METHOD_LABELS[slot.payment_method] : slot.is_primary_signatory ? 'Master lease deposit' : 'Not recorded'`
   - **Reference / UTR (`sm:col-span-2`):** `slot.payment_reference ? <span className="font-mono break-all">{slot.payment_reference}</span> : 'Not recorded'`
3. **Modal Footer (`flex items-center justify-between gap-3`):**
   - Left: `<button type="button" className={BUTTON_QUIET} onClick={() => onChangeRoom()}><BedDouble className="w-3.5 h-3.5 shrink-0" aria-hidden="true" /> {slot.room_id ? 'Change Room' : 'Link Room'}</button>` — closes the details modal and opens `RoomLinkModal`.
   - Right: `<button type="button" className={BUTTON_SECONDARY} onClick={onClose}>Close</button>`.

### 2.4. Render "Recent Deposit Activity" with `Room` Column & Atomic `From` / `To` Columns

1. **6 Center-Aligned Column Headers (Rule U3):**
   - `Date` (`w-[14%]`, `text-center`)
   - `Room` (`w-[13%]`, `text-center`) — renamed from `Slot / Space`, displaying `slot.room_number ?? slot.slot_label`.
   - `Activity` (`w-[21%]`, `text-center`)
   - `From` (`w-[18%]`, `text-center`)
   - `To` (`w-[18%]`, `text-center`)
   - `Amount` (`w-[16%]`, `text-center`)
2. **In the `Deposit Transaction Details` Modal:**
   - Replace the `Slot / Space: Room 1 (Slot 1)` fact with **`Room:`** `slot.room_number ?? slot.slot_label` (eliminating the `(Slot 1)` suffix).

### 2.5. Refine Top Ribbon Apartment Name (`Layout.tsx`)

1. In `frontend/src/components/Layout.tsx`:
   - Increase desktop `max-w` constraint from `lg:max-w-[300px]` to `lg:max-w-[420px]` so standard Indian society addresses don't truncate prematurely.
   - When `society_name` and `flat_number` exist on `apartment`, format cleanly with clear delimiter (e.g. `1805, CG6 · Supertech Capetown`).
   - Retain full text in `title` attribute for tooltip on hover.

---

## 3. Test & Verification Checklist

1. **Resident-First Escrow Pool Table & Deposit Details Modal (`SecurityDeposits.test.tsx`):**
   - Assert the 6 centered column headers in `Household deposit slots`: `['Resident', 'Room', 'Target Deposit', 'Funded Status', 'Paid Date', 'Actions']` (Rule U3).
   - Verify `Resident` is Column 1 (`<th scope="row">`) with the keyboard trigger button, and `Room` is Column 2.
   - Verify clicking a row opens `"Deposit Details"` (`role="dialog"`) showing the 9 facts (`Resident`, `Occupied Since`, `Room`, `Private Amenities`, `Outstanding Balance`, `Paid Date`, `Paid To`, `Payment Method`, `Reference / UTR`) with zero `"Slot 1"` duplication, and `Change Room` / `Link Room` in the footer.
2. **Recent Deposit Activity Card (`SecurityDeposits.test.tsx`):**
   - Assert the 6 centered column headers: `['Date', 'Room', 'Activity', 'From', 'To', 'Amount']`, and `"Room"` in `"Deposit Transaction Details"`.
   - Run vitest: `npm test -- --run` $\rightarrow$ all tests must pass.







