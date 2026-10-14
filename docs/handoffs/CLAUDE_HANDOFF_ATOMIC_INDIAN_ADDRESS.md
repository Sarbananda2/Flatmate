# Architectural Specification: Atomic Indian Residential Address & Localization

**Role:** Lead Architect & Domain Modeler  
**Audience:** Claude Code (Lead Backend & Frontend Implementer)  
**Status:** Approved for Implementation  
**Target Migration:** `0014_apartment_indian_address.sql`  
**Governing Standards:** Rule D1 (Strict Field Atomicity) and Rules U1–U6 in `docs/ARCHITECTURE.md`.  
**Governing Principle:** Fixed Indian Domestic Domain (`INR`, `Asia/Kolkata`, Indian Residential Address Structure).

---

## 1. Domain Context & Mandate

The application is built **exclusively for Indian shared households**.
1. **Fixed Financial & Temporal Constants:**
   - Currency is fixed to **Indian Rupee (`INR` / `₹`)**. No currency selectors or codes in user-facing UI.
   - Timezone is fixed to **Indian Standard Time (`Asia/Kolkata` / IST)**. No timezone clutter on apartment cards.
2. **Atomic Indian Residential Address:**
   - Currently, `apartment` stores an unstructured string lump (`"Flat 1805, CG6, Supertech Capetown"`).
   - Indian tenancy agreements, society gate passes, caretaker work, and HRA tax receipts require atomic address components: Flat Number, Tower/Block, Society/Apartment Complex, Locality/Sector, City, State, and 6-digit PIN Code.

---

## 2. Database Schema Migration (`0014_apartment_indian_address.sql`)

Create migration `aem/persistence/postgres/migrations/0014_apartment_indian_address.sql`:

```sql
-- Migration 0014: Atomic Indian Residential Address Structure

ALTER TABLE apartment
    ADD COLUMN flat_number TEXT,
    ADD COLUMN tower_block TEXT,
    ADD COLUMN society_name TEXT,
    ADD COLUMN locality_street TEXT,
    ADD COLUMN city TEXT,
    ADD COLUMN state TEXT,
    ADD COLUMN pincode TEXT CHECK (pincode IS NULL OR pincode ~ '^[1-9][0-9]{5}$');

-- Migrate existing live flat data if name matches the Capetown prototype
UPDATE apartment
SET flat_number = '1805',
    tower_block = 'CG6',
    society_name = 'Supertech Capetown',
    locality_street = 'Sector 74',
    city = 'Noida',
    state = 'Uttar Pradesh',
    pincode = '201301'
WHERE name LIKE '%Supertech%' OR name LIKE '%1805%';
```

---

## 3. Backend Services & API Updates

### 3.1. Apartment Model & Schemas (`aem/schemas/models.py`)
Update `ApartmentResponse` and `ApartmentUpdate` schemas:
- `flat_number`: `str | None = Field(None, max_length=50)`
- `tower_block`: `str | None = Field(None, max_length=50)`
- `society_name`: `str | None = Field(None, max_length=150)`
- `locality_street`: `str | None = Field(None, max_length=200)`
- `city`: `str | None = Field(None, max_length=100)`
- `state`: `str | None = Field(None, max_length=100)`
- `pincode`: `str | None = Field(None, pattern=r"^[1-9][0-9]{5}$")`
- Derived helper `premises_address`: compiles non-empty fields in standard Indian postal order:
  `"{flat_number}, {tower_block}, {society_name}, {locality_street}, {city}, {state} - {pincode}"`

### 3.2. Apartment Service (`aem/services/apartments.py` / `setup.py`)
- Support updating atomic fields in `update_apartment()`.
- Validate 6-digit PIN code syntax.
- Ensure HRA rent receipt generation uses `premises_address` for landlord tax proofs.

---

## 4. Frontend UI Specifications

### 4.1. Apartment Profile Card (`frontend/src/pages/services/RentConfig.tsx` / `HousingHub.tsx`)
1. **Clean Display Mode:**
   - Title: **Apartment & Property Details**
   - Action: `<Pencil /> Edit`
   - Fields layout (2-column or 3-column grid):
     - **Flat / Unit:** `Flat 1805` (or `1805`)
     - **Tower / Block:** `CG6` (or `Tower C`)
     - **Society / Complex:** `Supertech Capetown`
     - **Locality / Sector:** `Sector 74`
     - **City & State:** `Noida, Uttar Pradesh`
     - **PIN Code:** `201301`
   - **Completely Remove:** Currency and Time Zone fields. Do not show them on this card.
2. **Edit Modal (`EditApartmentModal`):**
   - Clicking `<Pencil /> Edit` opens a focused dialog with inputs for:
     - Flat Number
     - Tower / Wing / Block
     - Society / Complex Name
     - Locality / Sector / Street
     - City
     - State
     - 6-digit PIN Code (with instant client-side regex check `^[1-9][0-9]{5}$`)
   - Explicit `Cancel` and `Save Changes` buttons.

---

## 5. Verification Checklist

1. **Database Migration:**
   - Apply `0014_apartment_indian_address.sql` using migration role.
   - Verify existing apartment row is populated with atomic fields.
2. **API & Backend Tests:**
   - Update `tests/test_setup.py` / `tests/test_api.py` asserting atomic address CRUD and PIN code validation.
3. **Frontend Tests:**
   - Update `HousingHub.test.tsx` and `RentConfig.test.tsx` asserting the atomic fields render cleanly without currency/timezone clutter.
   - Verify `npm --prefix frontend test -- --run` passes cleanly.
4. **Live UI Check:**
   - Open `/housing?tab=lease` on port 8787:
     - Verify the Apartment card displays distinct fields (Flat, Tower, Society, Locality, City, PIN).
     - Verify Currency and Timezone are gone.
     - Click `<Pencil /> Edit`: verify modal opens with atomic inputs, edits, and saves cleanly.

