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

### 2.2. Pure Financial "Household Escrow Pool" Table (No Room Allotment Mixing — Rules U3, U7 & D1)

Physical room inventory and room allotment (`Room Number`, `Attached Washroom`, `Balcony`, `Occupied Since`, assigning/vacating rooms) belong exclusively to the **`Rooms Inventory`** tab (`/housing?tab=rooms`). The **`Security Deposit`** tab (`/housing?tab=deposits`) is strictly a **financial escrow ledger**.

1. **5 Center-Aligned Column Headers (Rule U3 — No `Room` Column):**
   - `Resident` (`w-[28%]`, `text-center`) — primary subject (`<th scope="row">`)
   - `Target Deposit` (`w-[18%]`, `text-center`)
   - `Funded Status` (`w-[18%]`, `text-center`)
   - `Paid Date` (`w-[18%]`, `text-center`)
   - `Actions` (`w-[18%]`, `text-center`)

2. **Single-Line Atomic Cells in `Household deposit slots`:**
   - **Column 1 (`Resident`, `<th scope="row">`, left-aligned cell under centered header):**
     - Focusable `<button type="button" aria-haspopup="dialog">` containing `<Avatar name={name} size="sm" />` + `<span className="font-bold text-[var(--text-primary)] truncate">{name}</span>` (or `<span className="font-semibold text-[var(--text-tertiary)]">Vacant</span>` when unassigned).
   - **Column 2 (`Target Deposit`):** `<Rupees paise={slot.target_deposit_paise} />` (`text-center font-bold`).
   - **Column 3 (`Funded Status`):** `<Pill tone={STATUS_TONES[slot.status]}>{STATUS_LABELS[slot.status]}</Pill>`.
   - **Column 4 (`Paid Date`):** `slot.funded_date ? formatDate(slot.funded_date) : '—'`.
   - **Column 5 (`Actions`):**
     - Only deposit lifecycle actions (`Pay`, `Vacate`, `Assign`), or `'—'` when none apply (such as the primary signatory's funded share).
     - Action button clicks call `event.stopPropagation()` so they open their own dialog without triggering the row click.

### 2.3. On-Click "Deposit Details" Modal (Purely Financial & Payment Receipt — No Room Fields, No `Change Room`)

Clicking any row in the `Household Escrow Pool` table opens a read-only `<Modal title="Deposit Details" description={slot.assigned_display_name ?? 'Vacant deposit share'}>`:

1. **Top Financial Summary Box (`rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3.5 space-y-3`):**
   - **Status Row:** `<span className="flex flex-wrap items-center gap-2"><Pill tone={STATUS_TONES[slot.status]}>{STATUS_LABELS[slot.status]}</Pill>{slot.is_primary_signatory && <Pill tone="accent">Primary signatory</Pill>}</span>`
   - **3-Column Financial Strip (`<dl className="m-0 grid grid-cols-3 gap-3 pt-2.5 border-t border-[var(--border-subtle)] text-xs">`):**
     1. **`Target Deposit`:** `<Rupees paise={slot.target_deposit_paise} className="font-bold tabular" />`
     2. **`Funded`:** `<Rupees paise={slot.funded_amount_paise} className="font-bold tabular" />`
     3. **`Outstanding Balance`:** `<Rupees paise={slot.outstanding_paise} className="font-bold tabular" />`

2. **Payment & Escrow Facts (`<dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-3.5 text-xs">` — No `Room`, `Private Amenities`, or `Occupied Since`):**
   1. **`Resident`:** `slot.assigned_display_name ? (slot.is_primary_signatory ? `${slot.assigned_display_name} (Primary signatory)` : slot.assigned_display_name) : 'Vacant'`
   2. **`Paid To`:** `slot.is_primary_signatory && slot.funded_amount_paise > 0 ? 'Direct to landlord' : slot.paid_to_display_name ?? (slot.funded_amount_paise > 0 ? 'Direct to landlord' : '—')`
   3. **`Paid Date`:** `slot.funded_date ? formatDate(slot.funded_date) : slot.is_primary_signatory ? 'Fronted with master deposit' : 'Not paid yet'`
   4. **`Payment Method`:** `slot.payment_method ? METHOD_LABELS[slot.payment_method] : slot.is_primary_signatory ? 'Master lease deposit' : 'Not recorded'`
   5. **`Reference / UTR` (`sm:col-span-2`):** `slot.payment_reference ? <span className="font-mono break-all">{slot.payment_reference}</span> : 'Not recorded'`

3. **Modal Footer (`flex justify-end`):**
   - Only `<button type="button" className={BUTTON_SECONDARY} onClick={onClose}>Close</button>`.
   - **Remove `Change Room` / `Link Room` and `RoomLinkModal` from `SecurityDeposits.tsx`.**

### 2.4. Resident-First "Recent Deposit Activity" Table & Modal (No `Room` Column or Fact)

1. **4 Center-Aligned Column Headers (Rule U3 — No `Room` Column):**
   - `Resident` (`w-[32%]`, `text-center`) — primary subject (`<th scope="row">`, left-aligned cell under centered header, containing a focusable `<button type="button" aria-haspopup="dialog">` with `<Avatar name={resident} size="sm" />` + `tx.resident_display_name ?? '—'`)
   - `Activity` (`w-[26%]`, `text-center`) — `<Pill tone={TX_TONES[tx.tx_type]}>{TX_LABELS[tx.tx_type]}</Pill>`
   - `Date` (`w-[20%]`, `text-center tabular`) — `formatDate(tx.tx_date)`
   - `Amount` (`w-[22%]`, `text-center tabular font-bold`) — `<Rupees paise={tx.amount_paise} />`

2. **On-Click "Deposit Transaction Details" Modal (`title="Deposit Transaction Details"`):**
   - **Subtitle / Description:** `${tx.resident_display_name ?? 'Resident'} · ${formatDate(tx.tx_date)}` (no `Room` in subtitle).
   - **Top Summary Banner:** `<Pill tone={TX_TONES[tx.tx_type]}>{TX_LABELS[tx.tx_type]}</Pill>` on the left, `<Rupees paise={tx.amount_paise} className="text-base font-bold tabular text-[var(--text-primary)]" />` on the right.
   - **2-Column Atomic Fact Grid (`<dl className="m-0 grid grid-cols-1 sm:grid-cols-2 gap-3.5 text-xs">` — No `Room` Fact):**
     1. **`From`:** `{from}` (derived via `txParties(tx, summary)`)
     2. **`To`:** `{to}` (derived via `txParties(tx, summary)`)
     3. **`Date`:** `{formatDate(tx.tx_date)}`
     4. **`Payment Method`:** `{txMethod(tx)}` (`UPI`, `Bank transfer`, `Retained from deposit`, or `Not recorded`)
     5. **`Reference / UTR` (`sm:col-span-2`):** `{tx.reference ? <span className="font-mono break-all">{tx.reference}</span> : 'Not recorded'}`
     6. **`Notes / Reason` (`sm:col-span-2`):** Full untruncated `{tx.notes || 'None'}`.
   - **Footer:** Right-aligned `<button type="button" className={BUTTON_SECONDARY} onClick={onClose}>Close</button>`.

### 2.5. Refine Top Ribbon Apartment Name (`Layout.tsx`)

1. In `frontend/src/components/Layout.tsx`:
   - Increase desktop `max-w` constraint from `lg:max-w-[300px]` to `lg:max-w-[420px]` so standard Indian society addresses don't truncate prematurely.
   - When `society_name` and `flat_number` exist on `apartment`, format cleanly with clear delimiter (e.g. `1805, CG6 · Supertech Capetown`).
   - Retain full text in `title` attribute for tooltip on hover.

---

## 3. Test & Verification Checklist

1. **Pure Financial Escrow Pool Table & Deposit Details Modal (`SecurityDeposits.test.tsx`):**
   - Assert the 5 centered column headers in `Household deposit slots`: `['Resident', 'Target Deposit', 'Funded Status', 'Paid Date', 'Actions']` (Rule U3 — no `Room` column).
   - Verify clicking a row opens `"Deposit Details"` (`role="dialog"`) showing the top financial strip (`Target Deposit`, `Funded`, `Outstanding Balance`) and the 5 payment/escrow facts (`Resident`, `Paid To`, `Paid Date`, `Payment Method`, `Reference / UTR`), with no `Room`, `Private Amenities`, `Occupied Since`, or `Change Room` / `Link Room` button.
2. **Resident-First Recent Deposit Activity Table & Transaction Modal (`SecurityDeposits.test.tsx`):**
   - Assert the 4 centered column headers: `['Resident', 'Activity', 'Date', 'Amount']` (Rule U3 — no `Room` column).
   - Verify clicking an activity row opens `"Deposit Transaction Details"` (`role="dialog"`) with `From`, `To`, `Date`, `Payment Method`, `Reference / UTR`, and `Notes / Reason` (no `Room` fact).
   - Run vitest: `npm test -- --run` $\rightarrow$ all tests must pass.










