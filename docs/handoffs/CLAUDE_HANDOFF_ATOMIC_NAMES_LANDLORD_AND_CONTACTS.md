# Architectural Specification: Atomic Person Names & Payout Beneficiary Toggle UX

**Role:** Lead Architect & Domain Modeler  
**Audience:** Claude Code (Lead Backend & Frontend Implementer)  
**Status:** Approved by User for Implementation  
**Target Migration:** `0017_atomic_person_names.sql`  
**Governing Standards:** Rule D1 (Strict Field Atomicity) and Rules U1–U6 in `docs/ARCHITECTURE.md`.  
**Entities Covered:** Immediate implementation for **Landlords / Owners & Household Contacts** (`household_contact` and `rent_lease_config`), establishing the architectural template for **Residents** (`resident`).

---

## 1. Domain Context & Mandates

### 1.1. Mandate A: Atomic Person Names (Input & Storage Breakdown)
Names of human stakeholders must be collected **atomically** from the user (`first_name`, `middle_name`, `last_name`) rather than as an unstructured text lump:
1. **At Collection (Input / Forms):**
   - The single "Name" input box is completely removed and overridden.
   - Forms present separate input fields:
     - **First Name \*** (required)
     - **Middle Name** (optional)
     - **Last Name** (optional — explicitly optional to support Indian mononyms and patronymics on PAN/Aadhaar)
   - Note: `PersonNameFields` component already exists in `frontend/src/components/Form.tsx` and can be utilized or mirrored.
2. **At Storage (Database & Models):**
   - Atomic components (`first_name`, `middle_name`, `last_name`) are stored in dedicated columns and serve as the authoritative Single Source of Truth (SSoT).
   - The canonical `name` / `landlord_name` column is retained and auto-derived/synced from the atomic components on every write (`compose_full_name(first, middle, last)`). This guarantees **100% backward compatibility** with all existing ledger passbooks, joins, receipts, audit logs, and search queries.
3. **At Display (Read-Only UI):**
   - Read-only views combine the components into a single clean string (`"Rajesh Kumar Sharma"`). Display surfaces never show fragmented "First / Middle / Last" fields.

### 1.2. Mandate B: Payout Beneficiary Toggle UX (Replacing Passive "Leave Blank" Text)
In the **Landlord Profile** modal (Tab 3: `Payout & Banking`) and the **Landlord Bank Account** modal in Payments:
1. **The UX Issue:**
   - The current field has an open text box with the placeholder `"Leave blank if payments go to the owner"` and hint `"Whose bank account or UPI ID receives payments if different from the owner."`
   - This causes user hesitation, ambiguity, and prompts users to retype the landlord's name into the box, creating string duplication and dirty data.
2. **The New Affirmative Toggle Control:**
   Replace the open text box with an affirmative **2-way segmented button toggle**:
   - **Option 1: `Same as Landlord ({landlordFullName})` (Default)**
     - Explicitly displays the landlord's dynamic full name pulled from Tab 1.
     - Hides the text input box completely.
     - Displays an affirmative status line:  
       `✓ Rent and deposits will be paid directly into the bank account of {landlordFullName}.`
     - Serializes `beneficiary_name = null` (or `payout_beneficiary_name = null` on lease).
   - **Option 2: `Custom Beneficiary`**
     - Clicking this toggle unfolds a single text field:
       * **Label:** `Beneficiary Name *`
       * **Placeholder:** `e.g. Anita Sharma`
       * **Hint:** `Whose bank account or UPI ID receives payouts (e.g., spouse, parent, family business).`
     - Validated as required only when the custom toggle is active.
     - Serializes `beneficiary_name = customBeneficiary.trim()`.

---

## 2. Database Schema Migration (`0017_atomic_person_names.sql`)

Create migration file `aem/persistence/postgres/migrations/0017_atomic_person_names.sql`:

```sql
-- Migration 0017: Atomic Person Names (Landlord, Contacts)
-- Breaks down unstructured name lumps into first_name, middle_name, and last_name,
-- keeping the canonical full name synced as the authoritative display representation.

-- 1. household_contact
ALTER TABLE household_contact
    ADD COLUMN first_name TEXT CHECK (first_name IS NULL OR length(trim(first_name)) > 0),
    ADD COLUMN middle_name TEXT CHECK (middle_name IS NULL OR length(trim(middle_name)) > 0),
    ADD COLUMN last_name TEXT CHECK (last_name IS NULL OR length(trim(last_name)) > 0);

-- Backfill household_contact from existing name
UPDATE household_contact
SET
    first_name = split_part(trim(name), ' ', 1),
    middle_name = CASE
        WHEN array_length(regexp_split_to_array(trim(name), '\s+'), 1) > 2
        THEN array_to_string((regexp_split_to_array(trim(name), '\s+'))[2:array_length(regexp_split_to_array(trim(name), '\s+'), 1)-1], ' ')
        ELSE NULL
    END,
    last_name = CASE
        WHEN array_length(regexp_split_to_array(trim(name), '\s+'), 1) >= 2
        THEN (regexp_split_to_array(trim(name), '\s+'))[array_length(regexp_split_to_array(trim(name), '\s+'), 1)]
        ELSE NULL
    END
WHERE first_name IS NULL;

-- 2. rent_lease_config
ALTER TABLE rent_lease_config
    ADD COLUMN landlord_first_name TEXT CHECK (landlord_first_name IS NULL OR length(trim(landlord_first_name)) > 0),
    ADD COLUMN landlord_middle_name TEXT CHECK (landlord_middle_name IS NULL OR length(trim(landlord_middle_name)) > 0),
    ADD COLUMN landlord_last_name TEXT CHECK (landlord_last_name IS NULL OR length(trim(landlord_last_name)) > 0);

-- Backfill rent_lease_config from existing landlord_name
UPDATE rent_lease_config
SET
    landlord_first_name = split_part(trim(landlord_name), ' ', 1),
    landlord_middle_name = CASE
        WHEN array_length(regexp_split_to_array(trim(landlord_name), '\s+'), 1) > 2
        THEN array_to_string((regexp_split_to_array(trim(landlord_name), '\s+'))[2:array_length(regexp_split_to_array(trim(landlord_name), '\s+'), 1)-1], ' ')
        ELSE NULL
    END,
    landlord_last_name = CASE
        WHEN array_length(regexp_split_to_array(trim(landlord_name), '\s+'), 1) >= 2
        THEN (regexp_split_to_array(trim(landlord_name), '\s+'))[array_length(regexp_split_to_array(trim(landlord_name), '\s+'), 1)]
        ELSE NULL
    END
WHERE landlord_first_name IS NULL;
```

---

## 3. Backend Implementation

### 3.1. Name Utility Functions (`aem/services/names.py`)
Ensure shared helper functions exist:
```python
def compose_full_name(first_name: str | None, middle_name: str | None, last_name: str | None) -> str:
    """Combines atomic name parts into a single normalized full name string."""
    parts = [p.strip() for p in (first_name, middle_name, last_name) if p and p.strip()]
    return " ".join(parts)

def split_full_name(raw_name: str) -> tuple[str, str | None, str | None]:
    """Deterministically splits a full name into (first, middle, last) for legacy payloads."""
    tokens = [t for t in (raw_name or "").strip().split() if t]
    if not tokens:
        return ("", None, None)
    if len(tokens) == 1:
        return (tokens[0], None, None)
    if len(tokens) == 2:
        return (tokens[0], None, tokens[1])
    return (tokens[0], " ".join(tokens[1:-1]), tokens[-1])
```

### 3.2. Pydantic Schemas (`aem/schemas/models.py`)
1. **`HouseholdContactSave` / `HouseholdContactUpdate`:**
   - `first_name: str | None = Field(None, min_length=1, max_length=100)`
   - `middle_name: str | None = Field(None, max_length=100)`
   - `last_name: str | None = Field(None, max_length=100)`
   - `name: str | None = Field(None, max_length=200)` (backward compatible: if `first_name` is omitted but `name` passed, split it)
   - `beneficiary_name: str | None = Field(None, max_length=200)`
2. **`HouseholdContactResponse`:**
   - Include `first_name`, `middle_name`, `last_name`, `name`, and `beneficiary_name`.
3. **`RentLeaseConfigSave`:**
   - `landlord_first_name: str | None = Field(None, min_length=1, max_length=100)`
   - `landlord_middle_name: str | None = Field(None, max_length=100)`
   - `landlord_last_name: str | None = Field(None, max_length=100)`
   - `landlord_name: str | None = Field(None, min_length=1, max_length=200)`
   - `payout_beneficiary_name: str | None = Field(None, max_length=200)`
4. **`RentLeaseConfigResponse`:**
   - Include `landlord_first_name`, `landlord_middle_name`, `landlord_last_name`, `landlord_name`, and `payout_beneficiary_name`.

### 3.3. Service & Repository Updates
1. **`aem/services/property.py`:**
   - In `create_contact()` / `update_contact()`:
     - If `first_name` is provided, compute canonical `name = compose_full_name(first_name, middle_name, last_name)`.
     - If only `name` is passed, split into `first_name`, `middle_name`, `last_name`.
     - Include atomic columns in repository operations.
2. **`aem/services/rent.py`:**
   - In `save_rent_config()`:
     - Compute canonical `landlord_name = compose_full_name(landlord_first_name, landlord_middle_name, landlord_last_name)`.
     - Save and return atomic fields alongside `landlord_name`.
3. **Repositories (`aem/persistence/repositories/property.py` & `rent.py`):**
   - Include atomic columns in `SELECT`, `INSERT`, and `UPDATE` statements.

---

## 4. Frontend UI & Form Implementation

### 4.1. TypeScript Types (`frontend/src/api/types.ts`)
- In `HouseholdContact`:
  ```ts
  first_name?: string | null
  middle_name?: string | null
  last_name?: string | null
  beneficiary_name?: string | null
  ```
- In `RentLeaseConfig`:
  ```ts
  landlord_first_name?: string | null
  landlord_middle_name?: string | null
  landlord_last_name?: string | null
  payout_beneficiary_name?: string | null
  ```

---

### 4.2. Contact Modal (`frontend/src/pages/services/PropertyContacts.tsx`)

#### A. Tab 1 (Contact Identity): Atomic Name Fields
Replace the single `<TextField label="Name" ... />` with the atomic name fields (e.g. using `<PersonNameFields />` or a 3-column grid):
```tsx
<div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
  <TextField
    label="First Name"
    value={firstName}
    onChange={setFirstName}
    required
    error={required(errors.firstName)}
    placeholder="e.g. Rajesh"
  />
  <TextField
    label="Middle Name"
    value={middleName}
    onChange={setMiddleName}
    placeholder="e.g. Kumar"
  />
  <TextField
    label="Last Name"
    value={lastName}
    onChange={setLastName}
    placeholder="e.g. Sharma"
  />
</div>
```
- **Validation:**
  - `firstName`: required, max 100 (`!firstName.trim() ? 'Enter first name.' : null`).
  - `middleName`: optional, max 100.
  - `lastName`: optional, max 100.
- **Computed Landlord Name:**
  ```tsx
  const landlordFullName = [firstName, middleName, lastName].map(s => s.trim()).filter(Boolean).join(' ') || contact?.name || 'Owner'
  ```

#### B. Tab 3 (Payout & Banking): 2-Way Beneficiary Toggle
Replace the open text input and `"Leave blank if..."` placeholder with the segmented button toggle:

```tsx
// State initialization:
const initialHasCustom = Boolean(contact?.beneficiary_name?.trim())
const [isCustomBeneficiary, setIsCustomBeneficiary] = useState(initialHasCustom)
const [customBeneficiary, setCustomBeneficiary] = useState(contact?.beneficiary_name ?? '')

// Render in Tab 3:
{uses.includes('owner') && (
  <div className="field">
    <label className="field-label">Payout Beneficiary</label>
    <div className="inline-flex p-1 bg-[var(--bg-elevated)] rounded-lg border border-[var(--border-medium)] gap-1">
      <button
        type="button"
        onClick={() => {
          setIsCustomBeneficiary(false)
        }}
        className={clsx(
          'px-3 py-1.5 text-xs font-semibold rounded-md transition-colors cursor-pointer',
          !isCustomBeneficiary
            ? 'bg-[var(--accent)] text-white shadow-xs'
            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
        )}
      >
        Same as Landlord ({landlordFullName})
      </button>
      <button
        type="button"
        onClick={() => setIsCustomBeneficiary(true)}
        className={clsx(
          'px-3 py-1.5 text-xs font-semibold rounded-md transition-colors cursor-pointer',
          isCustomBeneficiary
            ? 'bg-[var(--accent)] text-white shadow-xs'
            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
        )}
      >
        Custom Beneficiary
      </button>
    </div>

    {!isCustomBeneficiary ? (
      <p className="text-xs text-[var(--text-secondary)] mt-1.5">
        ✓ Rent and deposits will be paid directly into the bank account of <strong>{landlordFullName}</strong>.
      </p>
    ) : (
      <div className="mt-3">
        <TextField
          label="Beneficiary Name"
          value={customBeneficiary}
          onChange={setCustomBeneficiary}
          placeholder="e.g. Anita Sharma"
          required
          error={isCustomBeneficiary && !customBeneficiary.trim() ? 'Enter the beneficiary name.' : null}
          hint="Whose bank account or UPI ID receives payments if different from the owner."
        />
      </div>
    )}
  </div>
)}
```

- **Payload Serialization:**
  ```tsx
  beneficiary_name: uses.includes('owner') && isCustomBeneficiary ? customBeneficiary.trim() || null : null,
  ```

---

### 4.3. Landlord Account Modal (`frontend/src/pages/Payments.tsx`)
In `LandlordAccountModal`:
- Apply the same 2-way toggle for `Same as Landlord ({ownerName})` vs `Custom Beneficiary`.
- If `Same as Landlord` is selected, `payout_beneficiary_name` is set to `null`.
- If `Custom Beneficiary` is selected, display the input field for the custom name.

---

### 4.4. Read-Only Views
- Continue displaying the canonical full name as a single unified line:
  - Housing Hub: `Landlord: Rajesh Kumar Sharma`
  - Rent Hub: `Landlord: Rajesh Kumar Sharma`
  - Payment Pools & Passbooks: Payout destination displays `Anita Sharma` (if custom) or `Rajesh Kumar Sharma` (if same as owner).
- Do not split read-only display fields.

---

## 5. Verification Checklist

1. **Database Migration:**
   - Run `./scripts/manage.py init`.
   - Verify `household_contact` and `rent_lease_config` have `first_name`, `middle_name`, `last_name` populated properly.
2. **Backend Tests:**
   - Run:
     ```bash
     .venv/bin/pytest tests/test_property_contacts.py tests/test_rent.py tests/test_api.py
     ```
   - Assert atomic name fields save, auto-compose full name, and `beneficiary_name` handles both `null` and custom strings.
3. **Frontend Tests:**
   - Run:
     ```bash
     npm --prefix frontend test -- --run
     ```
   - Update tests in `PropertyContacts.test.tsx`, `Payments.test.tsx`, and `RentConfig.test.tsx` to reflect the new atomic name fields and the beneficiary toggle.
4. **End-to-End Live Check:**
   - Open `/housing?tab=contacts` (port 8787).
   - Edit Owner:
     - Tab 1: Enter First Name "Kumar", Last Name "Yadav".
     - Tab 3: Verify toggle defaults to `"Same as Landlord (Kumar Yadav)"` with confirmation text.
     - Switch to `"Custom Beneficiary"`: verify input field appears and requires a name. Enter "Sunita Yadav".
     - Save: verify Housing Hub displays Legal Owner "Kumar Yadav", while Payment Pool displays Payout Beneficiary "Sunita Yadav".
