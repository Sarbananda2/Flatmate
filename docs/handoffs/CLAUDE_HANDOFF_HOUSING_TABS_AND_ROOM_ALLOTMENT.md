# Architectural Specification: Housing Tab Sequence, Pure Financial Security Deposit & Dedicated Room Allotment Tab

**Role:** Lead Architect & UX Designer  
**Audience:** Claude Code (Lead Frontend Implementer)  
**Status:** Approved for Implementation  
**Governing Rules:** Rule U1 (Read-Only Default), Rule U3 (Centered Table Headers), Rule U5 (Concise Actions), Rule U6 (Zero Dead Space), Rule U7 (Summary-First Tables with On-Click Detail Modals), Rule D1 (Strict Field Atomicity)  
**Target Files:**
- `frontend/src/pages/housing/HousingHub.tsx`
- `frontend/src/pages/housing/HousingHub.test.tsx`
- `frontend/src/pages/services/PropertyRooms.tsx`
- `frontend/src/pages/services/PropertyRooms.test.tsx`
- `frontend/src/pages/services/SecurityDeposits.tsx`
- `frontend/src/pages/services/SecurityDeposits.test.tsx`

---

## 1. Domain Context & Separation of Concerns

Previously, physical room definition, resident room allotment, and security deposit escrow were tangled across `Security Deposit` and `Rooms Inventory`:
- `Security Deposit` showed room columns, private amenities (`Attached Washroom · Balcony`), `Occupied Since`, and a `Change Room` button.
- `Rooms Inventory` combined physical room definition (`Room Number`, `Base Rent`, `Attached Washroom`, `Balcony`) with resident allotment (`Current Occupant`, `Assign`, `Vacate`) and `Linked Escrow Slot`.

This specification establishes a clean, 4-stage domain progression at the start of `/housing`:
1. **`Tenancy & Master Lease` (`?tab=lease`)** — Master lease contract & property address.
2. **`Rooms Inventory` (`?tab=rooms`)** — **Room Defining only:** physical bedrooms (`Room Number`, `Attached Washroom`, `Balcony`, `Base Rent`) and `Add Room` / `Edit` / `Remove`.
3. **`Security Deposit` (`?tab=deposits`)** — **Financial Escrow only:** master deposit pool, resident deposit shares, payments, refunds, and recent deposit activity (zero room or amenity fields).
4. **`Room Allotment` (`?tab=allotment`)** — **Resident-to-Room Allotment only:** assigning and vacating residents across the defined rooms (`Room`, `Resident`, `Occupied Since`, `Actions`), with an on-click **`Room Allotment Details`** modal (Rule U7).
5. **`Stakeholders & Contacts` (`?tab=contacts`)**
6. **`Document Vault` (`?tab=documents`)**

---

## 2. Housing Hub Tab Bar (`frontend/src/pages/housing/HousingHub.tsx`)

Update `HOUSING_TABS` to the 6-tab sequence:
```ts
export const HOUSING_TABS = [
  { value: 'lease', label: 'Tenancy & Master Lease', icon: FileSignature },
  { value: 'rooms', label: 'Rooms Inventory', icon: BedDouble },
  { value: 'deposits', label: 'Security Deposit', icon: ShieldCheck },
  { value: 'allotment', label: 'Room Allotment', icon: DoorOpen },
  { value: 'contacts', label: 'Stakeholders & Contacts', icon: Users },
  { value: 'documents', label: 'Document Vault', icon: FileText },
] as const
```
- Render `<RoomsInventory />` when `tab === 'rooms'`.
- Render `<SecurityDepositSection />` when `tab === 'deposits'`.
- Render `<RoomAllotmentSection />` when `tab === 'allotment'`.

---

## 3. Tab 2: `Rooms Inventory` — Room Defining Only (`PropertyRooms.tsx`)

`RoomsInventory` defines the physical bedrooms in the apartment and their attributes — **not** who lives in them or which deposit slot they back:

1. **Panel Header:**
   - `title="Rooms Inventory"` (or `"Rooms Directory"`)
   - `hint`: `` `${data.summary.total_rooms} room${data.summary.total_rooms === 1 ? '' : 's'} defined` ``
   - `actions`: `<button type="button" className={BUTTON_PRIMARY} onClick={() => setDialog({ kind: 'add' })}><Plus ... /> Add Room</button>`
2. **5 Center-Aligned Column Headers (Rule U3 — No `Current Occupant` or `Linked Escrow Slot` Columns):**
   - `Room` (`text-center`) — `<th scope="row">`, `room.room_number`
   - `Attached Washroom` (`text-center`) — `<YesNo value={room.has_attached_washroom} />`
   - `Balcony` (`text-center`) — `<YesNo value={room.has_balcony} />`
   - `Base Rent` (`text-center`) — `room.base_rent_paise != null ? <Rupees paise={room.base_rent_paise} /> : '—'`
   - `Actions` (`text-center`) — `<Pencil /> Edit` button, plus `<Trash2 />` remove button when `!room.assigned_resident_id`.
3. **`RoomModal` (`Add Room` / `Edit Room X`):**
   - Contains **only** the physical room definition fields:
     - `Room Number` (`TextField`, required, unique)
     - `Attached Washroom` (`CheckboxField`)
     - `Balcony` (`CheckboxField`)
     - `Base Rent` (`MoneyInput`, optional)
   - **Remove** the `Deposit Slot` dropdown, `Occupant` dropdown, and `Moved in on` date field from `RoomModal`.

---

## 4. Tab 3: `Security Deposit` — Pure Financial Escrow (`SecurityDeposits.tsx`)

Strip all room-allotment fields (`Room` column, `Private Amenities`, `Occupied Since`, `Change Room` / `Link Room`, and `RoomLinkModal`) from `SecurityDeposits.tsx`:

1. **`Household Escrow Pool` Table (5 Center-Aligned Headers — Rule U3):**
   - `Resident` (`w-[28%]`, `<th scope="row">`, left-aligned cell under centered header with focusable `<button type="button" aria-haspopup="dialog">` containing `<Avatar size="sm" />` + `slot.assigned_display_name`, or `Vacant`)
   - `Target Deposit` (`w-[18%]`, `text-center`)
   - `Funded Status` (`w-[18%]`, `text-center`)
   - `Paid Date` (`w-[18%]`, `text-center`)
   - `Actions` (`w-[18%]`, `text-center`) — only `Pay`, `Vacate`, `Assign`, or `'—'`.
2. **On-Click `"Deposit Details"` Modal (`title="Deposit Details"`):**
   - `description`: `slot.assigned_display_name ?? 'Vacant deposit share'`.
   - **Top Financial Summary Box:** Status `<Pill>` (plus `<Pill tone="accent">Primary signatory</Pill>` if applicable), and 3-column financial `<dl>` strip underneath: `Target Deposit`, `Funded`, and `Outstanding Balance`.
   - **Payment & Escrow Facts (2-Column `<dl>` — 5 Facts):**
     1. `Resident`
     2. `Paid To`
     3. `Paid Date`
     4. `Payment Method`
     5. `Reference / UTR` (`sm:col-span-2`, in `font-mono`)
   - **Footer:** Right-aligned `Close` button only (no `Change Room` / `Link Room`).
3. **`Recent Deposit Activity` Table & `"Deposit Transaction Details"` Modal (4 Center-Aligned Headers — No `Room`):**
   - Table headers: `Resident` (`w-[32%]`), `Activity` (`w-[26%]`), `Date` (`w-[20%]`), `Amount` (`w-[22%]`).
   - Clicking a row opens `"Deposit Transaction Details"` with `From`, `To`, `Date`, `Payment Method`, `Reference / UTR`, and `Notes / Reason` (no `Room` field).

---

## 5. Tab 4: `Room Allotment` — Resident-to-Room Assignment (`PropertyRooms.tsx`)

Create and export `RoomAllotmentSection` in `frontend/src/pages/services/PropertyRooms.tsx`:

1. **Panel Header:**
   - `title="Room Allotment"`
   - `icon={<DoorOpen className="w-4 h-4 shrink-0 text-[var(--accent)]" aria-hidden="true" />}`
   - `hint`: `` `${data.summary.occupied_rooms} of ${data.summary.total_rooms} rooms allotted · ${data.summary.vacant_rooms} vacant` ``
2. **Empty State (When No Rooms Defined Yet):**
   - Render `<EmptyBlock title="No rooms defined yet" body="Define the flat's bedrooms in Rooms Inventory first, then allot residents to them here." action={<Link to="/housing?tab=rooms" className={BUTTON_PRIMARY}>Go to Rooms Inventory</Link>} />`.
3. **4 Center-Aligned Column Headers (Rule U3 & Rule U7):**
   - `Room` (`w-[22%]`, `text-center`) — `<th scope="row">`, focusable `<button type="button" aria-haspopup="dialog">` displaying `room.room_number` so clicking the row or pressing `Enter`/`Space` opens `"Room Allotment Details"`.
   - `Resident` (`w-[32%]`, `text-center`) — single-line `<Avatar size="sm" />` + `room.assigned_display_name` (left-aligned cell under centered header), or `<Pill tone="warning">Vacant</Pill>` when unassigned.
   - `Occupied Since` (`w-[22%]`, `text-center tabular`) — `room.assigned_since ? formatDate(room.assigned_since) : '—'`.
   - `Actions` (`w-[24%]`, `text-center`) —
     - `<button type="button" className={BUTTON_SECONDARY}><UserPlus /> {room.assigned_resident_id ? 'Reassign' : 'Assign'}</button>` (opens `AssignModal`)
     - When `room.assigned_resident_id` is set: `<button type="button" className={BUTTON_QUIET}><DoorOpen /> Vacate</button>` (opens `Vacate Room` confirm dialog).
     - Action buttons call `event.stopPropagation()` so clicking them does not open the row's details modal.
4. **On-Click `"Room Allotment Details"` Modal (Rule U7):**
   - Clicking a row in `Room Allotment` opens `<Modal title="Room Allotment Details" description={room.room_number}>`:
     - **Top Banner:** `<Pill tone={room.assigned_resident_id ? 'success' : 'warning'}>{room.assigned_resident_id ? 'Occupied' : 'Vacant'}</Pill>` on the left, and `room.base_rent_paise != null ? <Rupees paise={room.base_rent_paise} className="font-bold tabular" /> : 'Base rent not set'` on the right.
     - **2-Column Atomic Fact Grid (`<dl>`):**
       1. `Room`: `room.room_number`
       2. `Private Amenities`: `Attached Washroom` / `Balcony` joined by ` · `, or `'None'`
       3. `Resident`: `room.assigned_display_name ?? 'Vacant'`
       4. `Occupied Since`: `room.assigned_since ? formatDate(room.assigned_since) : '—'`
     - **Footer:** Right-aligned `Close` button.

---

## 6. Verification & Test Checklist

1. **`HousingHub.test.tsx`:**
   - Assert the 6 tabs in order: `Tenancy & Master Lease`, `Rooms Inventory`, `Security Deposit`, `Room Allotment`, `Stakeholders & Contacts`, `Document Vault`.
   - Verify `?tab=allotment` renders the `Room Allotment` panel.
2. **`PropertyRooms.test.tsx`:**
   - Verify `Rooms Inventory` renders only physical room definition columns (`Room`, `Attached Washroom`, `Balcony`, `Base Rent`, `Actions`) and `RoomModal` only edits physical room attributes.
   - Verify `Room Allotment` renders `Room`, `Resident`, `Occupied Since`, and `Actions` (`Assign` / `Reassign`, `Vacate`), and clicking a row opens `"Room Allotment Details"` (Rule U7).
3. **`SecurityDeposits.test.tsx`:**
   - Verify `Household Escrow Pool` (5 columns) and `Recent Deposit Activity` (4 columns) and their detail modals contain zero room-allotment fields.
   - Run `npm test -- --run` and `npm run build` $\rightarrow$ all tests pass cleanly.

