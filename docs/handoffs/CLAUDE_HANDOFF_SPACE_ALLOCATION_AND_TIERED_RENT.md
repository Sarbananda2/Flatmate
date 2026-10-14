# Architectural Specification: Space Allocation Linking & Room-Tiered Rent (DEBT-006)
## Step 2 of Post-Phase 2 Roadmap

**Role:** Lead Architect & Financial Systems Designer  
**Audience:** Claude Code (Lead Backend & Frontend Implementer)  
**Status:** Approved for Implementation  
**Target Migration:** `0012_room_base_rent.sql`  
**Target Areas:** `aem/services/rooms.py`, `aem/services/deposits.py`, `aem/services/rent.py`, `frontend/src/pages/services/`

---

## 1. Executive Summary & Domain Context

In shared apartment co-living, physical space and financial liability are inextricably linked:
1. **Space-Escrow Bridge:** Each physical room is backed by a specific security deposit slot (`household_deposit_slot.room_id`). Previously, this foreign key was populated in the database by scripts, but was not surfaced or editable in the UI.
2. **Room-Tiered Rent Allocation (Model 2):** Rooms with distinct amenities (attached washroom, private balcony) command different market rents. Hardcoding rent splits to `equal` forces roommates to manually compute and adjust offsets outside the app.

This milestone connects physical room assets to financial escrow slots across the UI, and establishes **Room-Tiered Rent (Model 2)** in the rent generation engine.

---

## 2. Invariants & Business Rules

* **Invariant S1 (Single Active Room-to-Slot Association):** A deposit slot can be linked to at most one room, and a room can be linked to at most one deposit slot at any time (`UNIQUE (apartment_id, room_id)` on `household_deposit_slot`).
* **Invariant S2 (Tiered Rent Balancing):** If rent is generated using `split_mode = "room_tiered"`, every participating resident must occupy a room with a defined `base_rent_paise`, and the sum of room rents for participants must equal `monthly_rent_paise` on the lease.
* **Invariant S3 (Design System Compliance):** All new or modified data tables must have center-aligned column headers (`<th className="... text-center">`), and all input fields must perform immediate client-side validation.

---

## 3. Database Migration (`0012_room_base_rent.sql`)

Create migration `aem/persistence/postgres/migrations/0012_room_base_rent.sql`:

```sql
-- Migration 0012: Room Base Rent for Tiered Allocation (Model 2)

ALTER TABLE household_room
    ADD COLUMN base_rent_paise BIGINT CHECK (base_rent_paise IS NULL OR base_rent_paise >= 0);

-- Enforce 1-to-1 relationship between an active room and a deposit slot
CREATE UNIQUE INDEX idx_deposit_slot_unique_room
    ON household_deposit_slot(apartment_id, room_id)
    WHERE room_id IS NOT NULL;
```

---

## 4. Backend Service & API Requirements

### 4.1. Rooms Service (`aem/services/rooms.py`) & Router (`aem/api/routers/rooms.py`)
1. **Extend Room Model & Repository:**
   - Store and retrieve `base_rent_paise`.
   - In `list_rooms(db, apartment_id)`:
     - Join or lookup `household_deposit_slot` to include `linked_deposit_slot`:
       ```json
       {
         "slot_id": "slot_123",
         "slot_number": 1,
         "slot_label": "Room 1 Deposit",
         "target_deposit_paise": 5000000,
         "funded_amount_paise": 5000000,
         "status": "occupied_funded"
       }
       ```
     - Include `summary.total_base_rent_paise` in the returned summary dictionary.
2. **Room Create / Update Payload:**
   - Support `base_rent_paise` in `RoomCreate` and `RoomUpdate` schemas (`aem/schemas/models.py`).
   - Support optional `linked_slot_id: str | None` in room creation/update to associate a slot directly.

### 4.2. Deposits Service (`aem/services/deposits.py`) & Router (`aem/api/routers/deposits.py`)
1. **Enrich Deposit Summary:**
   - In `deposit_summary()` / `_summary()`:
     - For every slot in `slots`, augment with room details if `room_id` is present:
       - `room_number`: string (e.g. "Room 1")
       - `has_attached_washroom`: bool
       - `has_balcony`: bool
       - `base_rent_paise`: int | None
2. **Allow Slot-to-Room Assignment in API:**
   - In `DepositSlotItem` schema, add `room_id: str | None = None`.
   - In `save_slots()`: persist `room_id`.
   - Add explicit route:
     `POST /api/rent/deposits/slots/{slot_id}/link-room`
     Payload: `{"room_id": str | None}` (passing `null` unlinks).

### 4.3. Rent Engine & Tiered Allocation (`aem/services/rent.py`)
1. **Tiered Preview & Generation:**
   - Update `_preview` and `generate_monthly_rent_cycle`:
     - Accept parameter `split_mode: Literal["equal", "room_tiered"] = "equal"`.
     - When `split_mode == "room_tiered"`:
       - Find the occupied room for each participant in `chosen`.
       - If any participant does not have an assigned room, or their room has no `base_rent_paise`, raise `ValidationError` ("All participating residents must occupy a room with a configured base rent.").
       - Calculate each resident's share from their room's `base_rent_paise`.
       - If the sum of room rents does not match the month's total rent, reject with `ValidationError` ("The sum of room rents (₹X) does not equal the monthly lease rent (₹Y).").
2. **API Route Update (`aem/api/routers/rent.py`):**
   - Accept `split_mode` in rent cycle preview and generate request payloads (`RentGenerateRequest`).

---

## 5. Frontend UX & UI Specifications

### 5.1. Rooms Inventory (`frontend/src/pages/services/PropertyRooms.tsx`)
1. **Table Columns:**
   All headers center-aligned (`text-center`):
   - `Room Number`
   - `Base Rent` (formatted with `<Rupees paise={...} />`, or "—" if not set)
   - `Attached Washroom` (Pill badge)
   - `Balcony` (Pill badge)
   - `Current Occupant` (Avatar/Name + Occupied Since)
   - `Linked Escrow Slot` (Badge showing `Slot X: ₹Y`, color-coded by funded status: green for funded, amber for pending, gray for unlinked)
   - `Actions` (Edit, Assign/Swap, Vacate, Delete)
2. **Add / Edit Room Modal:**
   - Add input for **Base Rent (₹)** with rupees-to-paise conversion and non-negative validation.
   - Add dropdown to link to an existing unlinked **Deposit Slot**.

### 5.2. Security Deposits Management (`frontend/src/pages/services/SecurityDeposits.tsx`)
1. **Slots Table:**
   - In the `Slot / Space` column:
     - Display `slot.slot_label` and `Slot {slot.slot_number}`.
     - If linked to a room, display a compact pill: `Room {slot.room_number}` with subtle icons for washroom (`🚿`) and balcony (`🌅`).
2. **Edit Slot Dialog:**
   - Allow reassigning or unlinking the associated room from a dropdown of available apartment rooms.

### 5.3. Rent Cycle Generation (`frontend/src/pages/services/Rent.tsx`)
1. **Generation Dialog / Preview Card:**
   - If rooms have `base_rent_paise` configured and sum matches the lease rent:
     - Provide a toggle or radio selector: **Split Mode: Equal Share vs. Room-Tiered**.
     - Selecting "Room-Tiered" displays each resident's share matched to their room's base rent.

---

## 6. Verification Checklist

1. **Database Migration:**
   - Run `manage.py backup` before applying.
   - Apply `0012_room_base_rent.sql` using migration role.
2. **Backend Automated Tests:**
   - `tests/test_rooms.py`: Test room CRUD with `base_rent_paise` and linked deposit slot serialization.
   - `tests/test_deposits.py`: Test slot room linking, unlinking, and unique constraint enforcement.
   - `tests/test_rent.py`: Test `split_mode="room_tiered"` generation, validation errors when unassigned, and matching share calculations.
3. **Frontend Automated Tests:**
   - Vitest component tests in `PropertyRooms.test.tsx`, `SecurityDeposits.test.tsx`, and `Rent.test.tsx` ensuring clean rendering, centered `<th>`, and form validations.
4. **Live Verification:**
   - Navigate to `/services/property` and `/services/deposits` on `http://127.0.0.1:8787` to verify bi-directional room-slot visibility.

