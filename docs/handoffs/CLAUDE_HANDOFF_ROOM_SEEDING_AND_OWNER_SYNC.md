# Technical Handoff: Live Room Seeding & Owner-Lease Synchronization
## Step 1 of Post-Phase 2 Roadmap

**Role:** Lead Architect & Financial Systems Designer  
**Audience:** Claude Code (Lead Backend & Frontend Implementer)  
**Status:** Approved for Implementation  
**Target Areas:** `aem/services/property.py`, `aem/services/rent.py`, `scripts/seed_live_rooms.py`, `tests/`

---

## 1. Objectives

1. **Live Room Seeding & Deposit Slot Linking:**
   - Seed the 3 real rooms into the active apartment:
     - **Room 1:** Occupant = Rohan
     - **Room 2:** Occupant = Arnab
     - **Room 3:** Occupant = Sarbananda
   - Link each resident's existing `household_deposit_slot` to their corresponding room (`household_deposit_slot.room_id = household_room.room_id`).
   
2. **Bi-Directional Owner $\leftrightarrow$ Lease Landlord Synchronization:**
   - Eliminate dual source-of-truth divergence between `household_contact` (role = `'owner'`) and `rent_lease_config` (`landlord_*`).
   - Updating or creating an owner contact must keep the lease configuration in sync.
   - Updating the lease configuration must keep the owner contact in sync.
   - If an owner exists on the lease but not in contacts (or vice versa), propagate the record.

---

## 2. Business Rules & Synchronization Logic

### Rule 1: Field Mapping Matrix
The following fields are strictly synchronized:

| `household_contact` (role = 'owner') | `rent_lease_config` | Validation Rules |
| :--- | :--- | :--- |
| `name` | `landlord_name` | Non-empty text, $\le$ 200 chars |
| `phone` | `landlord_phone` | 7–15 digits, E.164/standard format |
| `email` | `landlord_email` | Valid email regex |
| `pan` | `landlord_pan` | `^[A-Z]{5}[0-9]{4}[A-Z]$` |
| `bank_account_no` | `landlord_bank_account` | 9–18 digits |
| `bank_ifsc` | `landlord_bank_ifsc` | `^[A-Z]{4}0[A-Z0-9]{6}$` |
| `upi_id` | `landlord_upi_id` | `^[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}$` |

### Rule 2: Synchronizing from `household_contact` to `rent_lease_config`
- When `create_contact()` or `update_contact()` runs in `aem.services.property` with `role == 'owner'`:
  - Check if `rent_lease_config` exists for the apartment.
  - If a lease exists, update the `landlord_*` fields in the same database transaction.
  - Record the audit entry for field changes on `rent_lease_config`.

### Rule 3: Synchronizing from `rent_lease_config` to `household_contact`
- When `save_lease_config()` runs in `aem.services.rent`:
  - Check if any contact with `role == 'owner'` exists for this `apartment_id`.
  - If one exists: update its `name`, `phone`, `email`, `pan`, `bank_account_no`, `bank_ifsc`, `upi_id`.
  - If none exists: create a new `household_contact` row with `role = 'owner'`.
  - Record audit trail under `household_contact`.

### Rule 4: Idempotent Live Seeding Script (`scripts/seed_live_rooms.py`)
Create a CLI utility runnable via `.venv/bin/python scripts/seed_live_rooms.py`:
1. Find the apartment (e.g. `apt_main` or default).
2. Fetch active residents: match Rohan, Arnab, and Sarbananda by name or ID.
3. For each room (Room 1, Room 2, Room 3):
   - Check if the room already exists in `household_room`. If not, insert with appropriate default flags (e.g. Room 1 attached washroom, Room 2 balcony, or standard).
   - Assign the resident with `assigned_since = '2026-05-01'` (or the flatmate's move-in / lease date).
4. Link `household_deposit_slot`:
   - Find deposit slots matching the resident or slot order.
   - Update `household_deposit_slot.room_id = household_room.room_id`.
5. Sync existing Landlord from `rent_lease_config` to `household_contact` if no owner contact currently exists.

---

## 3. Verification & Acceptance Criteria

1. **Unit Tests:**
   - `test_owner_contact_syncs_to_lease`: Modifying owner contact in `property.py` reflects immediately in `rent.get_lease_config()`.
   - `test_lease_save_syncs_to_owner_contact`: Saving lease in `rent.py` updates or creates the `household_contact` record.
2. **Database Integrity:**
   - Running `scripts/seed_live_rooms.py` assigns Room 1, 2, and 3 to Rohan, Arnab, and Sarbananda without violating constraints.
   - Deposit slots have valid foreign keys in `household_deposit_slot.room_id`.
3. **Frontend UI Check:**
   - `/services/property` (Rooms Inventory tab) shows Room 1 (Occupied: Rohan), Room 2 (Occupied: Arnab), Room 3 (Occupied: Sarbananda).
   - `/services/property?tab=contacts` shows Owner (Kumar Yadav or configured landlord) populated with Bank, PAN, and UPI.
   - `/residents/:id` for each flatmate shows their assigned space and amenities.

