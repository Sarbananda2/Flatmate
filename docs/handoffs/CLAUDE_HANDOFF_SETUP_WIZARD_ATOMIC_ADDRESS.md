# Architectural Specification: Setup Wizard Atomic Indian Address Onboarding

**Role:** Lead Architect & Domain Modeler  
**Audience:** Claude Code (Lead Backend & Frontend Implementer)  
**Status:** Approved by User for Implementation  
**Governing Standards:** Rule D1 (Strict Field Atomicity), Rule U1 (Unified Presentation), and Fixed Indian Domestic Domain (`INR`, `Asia/Kolkata`).  
**Files Affected:**
- Frontend: `frontend/src/pages/SetupWizard.tsx`, `frontend/src/pages/SetupWizard.test.tsx`
- Backend: `aem/schemas/models.py`, `aem/services/setup.py`, `aem/api/routers/setup.py`, `tests/test_setup.py`

---

## 1. Domain Context & Mandate

### The Problem in Current Step 1 of Setup Wizard:
1. **Unstructured String Lump (Rule D1 Violation):**
   Step 1 asks for a single unstructured text input: `Apartment name *` (with placeholder `e.g. Palm Grove 302`).
   This contradicts the rest of the application (Migration 0014), where residential properties require structured atomic components (Flat, Tower, Society, Locality, City, State, PIN Code).
   Onboarding an apartment with only an unstructured name leaves `flat_number`, `society_name`, `city`, etc., as `NULL`, breaking HRA receipts and lease summaries until manually edited later.
2. **Internal Developer Jargon in UI Copy:**
   The hint text says: *"The short identifier used in exports is worked out from this."*
   Exposing backend slugification (`slugify(name)`) to a user setting up their household is an anti-pattern.
3. **Outdated Copy:**
   *"Only the name. Everything else has a sensible default you can change later."*
   This referred to the legacy currency and timezone dropdowns, which have since been locked to fixed Indian constants (`INR` and `Asia/Kolkata`).
4. **Layout Ergonomics:**
   A single text box stretched across a massive desktop card with an isolated `Continue` button.

---

## 2. Target Design: Atomic Address Onboarding

In **Step 1 · Where is your apartment?**, replace the single text input with a clean, structured Indian residential address form:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Step 1 · Where is your apartment?                                            │
│ Enter your apartment and address details. These will be used on your         │
│ household dashboard, lease terms, and HRA rent receipts.                     │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ ┌──────────────────────────────────────┐ ┌─────────────────────────────────┐ │
│ │ Flat / Unit Number *                 │ │ Tower / Wing / Block            │ │
│ │ [ 1805                             ] │ │ [ CG6                         ] │ │
│ └──────────────────────────────────────┘ └─────────────────────────────────┘ │
│                                                                              │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ Society / Complex Name *                                                 │ │
│ │ [ Supertech Capetown                                                   ] │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│ ┌──────────────────────────────────────┐ ┌─────────────────────────────────┐ │
│ │ Locality / Sector / Street           │ │ City *                          │ │
│ │ [ Sector 74                        ] │ │ [ Noida                       ] │ │
│ └──────────────────────────────────────┘ └─────────────────────────────────┘ │
│                                                                              │
│ ┌──────────────────────────────────────┐ ┌─────────────────────────────────┐ │
│ │ State                                │ │ PIN Code                        │ │
│ │ [ Uttar Pradesh                    ] │ │ [ 201301                      ] │ │
│ └──────────────────────────────────────┘ └─────────────────────────────────┘ │
│                                                                              │
│                                                                [ Continue ]  │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Validation Rules:**
  - `Flat Number`: Required (max 50 chars).
  - `Society / Complex Name`: Required (max 150 chars).
  - `City`: Required (max 100 chars).
  - `Tower / Block`, `Locality`, `State`: Optional.
  - `PIN Code`: Optional, but if typed, strictly validated against 6 digits (`^[1-9][0-9]{5}$`).
- **Canonical Apartment Name Auto-Derivation:**
  - `name = f"{society_name} {flat_number}".strip()` (e.g. `"Supertech Capetown 1805"`).
  - If a legacy payload sends `name`, preserve it.

---

## 3. Backend Implementation

### 3.1. Schemas (`aem/schemas/models.py`)
Update `SetupApartmentRequest`:
```python
class SetupApartmentRequest(ApiModel):
    name: str | None = Field(None, max_length=200,
                             description="Friendly name. If omitted, derived from society and flat number.")
    flat_number: str | None = Field(None, max_length=50)
    tower_block: str | None = Field(None, max_length=50)
    society_name: str | None = Field(None, max_length=150)
    locality_street: str | None = Field(None, max_length=200)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=100)
    pincode: str | None = Field(None, pattern=r"^[1-9][0-9]{5}$")
    currency_code: str = Field("INR", min_length=3, max_length=3)
    timezone: str = Field("Asia/Kolkata", min_length=1, max_length=64)
    apartment_id: str | None = Field(None, max_length=64)
```

### 3.2. Setup Service (`aem/services/setup.py`)
In `create_apartment_step(...)`:
1. Compute `name`:
   ```python
   if not name:
       parts = [p.strip() for p in (society_name, flat_number) if p and p.strip()]
       name = " ".join(parts) if parts else "My Apartment"
   ```
2. Create the apartment via `apartments.create_apartment(...)`.
3. Update atomic address fields via `apartments.update_apartment(...)` (or update `create_apartment` to accept address fields):
   ```python
   apartments.update_apartment(
       db, apartment_id=resolved_id,
       flat_number=flat_number, tower_block=tower_block, society_name=society_name,
       locality_street=locality_street, city=city, state=state, pincode=pincode,
       actor=actor
   )
   ```

---

## 4. Frontend UI Implementation (`frontend/src/pages/SetupWizard.tsx`)

### 4.1. Draft State
Update `SetupDraft` to hold atomic address fields:
```ts
interface SetupDraft {
  flat_number: string
  tower_block: string
  society_name: string
  locality_street: string
  city: string
  state: string
  pincode: string
  // ... existing residents, picked, amounts, custom ...
}
```

### 4.2. Form Markup (Step 1)
Replace the single `TextField` with the responsive address grid:
```tsx
<h2 className="card-title">Step 1 · Where is your apartment?</h2>
<div className="card-hint">
  Enter your apartment and address details. These will be used on your household dashboard, lease terms, and HRA rent receipts.
</div>

<div className="grid grid-cols-1 md:grid-cols-2 gap-4">
  <TextField
    label="Flat / Unit Number"
    value={draft.flat_number}
    onChange={(v) => patch({ flat_number: v })}
    required
    placeholder="e.g. 1805"
    error={touched && !draft.flat_number.trim() ? 'Enter flat/unit number.' : null}
  />
  <TextField
    label="Tower / Wing / Block"
    value={draft.tower_block}
    onChange={(v) => patch({ tower_block: v })}
    placeholder="e.g. CG6 or Tower B"
  />
  <div className="md:col-span-2">
    <TextField
      label="Society / Complex Name"
      value={draft.society_name}
      onChange={(v) => patch({ society_name: v })}
      required
      placeholder="e.g. Supertech Capetown"
      error={touched && !draft.society_name.trim() ? 'Enter society/complex name.' : null}
    />
  </div>
  <TextField
    label="Locality / Sector / Street"
    value={draft.locality_street}
    onChange={(v) => patch({ locality_street: v })}
    placeholder="e.g. Sector 74"
  />
  <TextField
    label="City"
    value={draft.city}
    onChange={(v) => patch({ city: v })}
    required
    placeholder="e.g. Noida"
    error={touched && !draft.city.trim() ? 'Enter city.' : null}
  />
  <TextField
    label="State"
    value={draft.state}
    onChange={(v) => patch({ state: v })}
    placeholder="e.g. Uttar Pradesh"
  />
  <TextField
    label="PIN Code"
    value={draft.pincode}
    onChange={(v) => patch({ pincode: v })}
    inputMode="numeric"
    maxLength={6}
    placeholder="201301"
    hint="6 digits"
    error={pincodeError}
  />
</div>
```

### 4.3. Payload on Continue
```ts
await api.post<{ state: SetupState }>('/api/setup/apartment', {
  flat_number: draft.flat_number.trim(),
  tower_block: draft.tower_block.trim() || null,
  society_name: draft.society_name.trim(),
  locality_street: draft.locality_street.trim() || null,
  city: draft.city.trim(),
  state: draft.state.trim() || null,
  pincode: draft.pincode.trim() || null,
})
```

---

## 5. Verification Checklist

1. **Backend Tests (`tests/test_setup.py`):**
   - Assert `create_apartment_step` accepts atomic address fields and returns the apartment with `premises_address` populated.
   - Run:
     ```bash
     .venv/bin/pytest tests/test_setup.py tests/test_apartments.py
     ```
2. **Frontend Tests (`frontend/src/pages/SetupWizard.test.tsx`):**
   - Update tests to fill atomic address fields (`flat_number`, `society_name`, `city`) instead of single `apartmentName`.
   - Run:
     ```bash
     npm --prefix frontend test -- --run
     ```
3. **Live UI Verification:**
   - Open `http://localhost:8787/setup`.
   - Verify Step 1 renders the structured Indian address form with natural copy and no developer jargon.
   - Fill in Flat `1805`, Tower `CG6`, Society `Supertech Capetown`, City `Noida`, PIN `201301`, and click Continue.
   - Verify it advances smoothly to Step 2.

