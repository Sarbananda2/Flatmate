# Architectural Specification: Enforcement of Read-Only UI Standards (Rules U1–U4)

**Role:** Lead Architect & UX Designer  
**Audience:** Claude Code (Lead Frontend & Backend Implementer)  
**Status:** Approved for Implementation  
**Governing Standard:** Section "User Interface & Design System Standards" in `docs/ARCHITECTURE.md`

---

## 1. Domain Context & Mandate

Across several areas of the app, static configurations that have already been saved are permanently rendered in "edit mode" (live text inputs, dropdowns, radio groups, and Save buttons). Furthermore, in some screens the saved summary and the live editor are displayed simultaneously.

This handoff enforces **Rule U1 (Read-Only by default for saved state)** and **Rule U2 (Strict mutual exclusion of display view and editor)** across all existing screens.

---

## 2. Invariants & Implementation Patterns

### Invariant U1: Read-Only by Default
* If an entity is already configured (`data != null`), it must render as a clean, polished **Read-Only Display Card** with typography, badges, and formatted values (`<Rupees paise={...} />`, `formatDate(...)`).
* Static input fields (`<TextField>`, `<SelectField>`, `<MoneyInput>`, `<DateField>`) must **never** be rendered on screen unless the user is actively in edit mode (or during first-time initial creation).

### Invariant U2: Mutual Exclusion (Never Show View & Editor Simultaneously)
* The user must click an explicit **"Edit"** button to enter edit mode.
* When edit mode is active:
  * **Option A (Modal/Dialog - Preferred):** Opens a focused `<Modal>` with the form inputs, `Cancel`, and `Save Changes` buttons.
  * **Option B (In-Place Toggle):** The card flips from Display state to Form state, replacing display labels with form inputs and `Cancel` / `Save` buttons. On save or cancel, it flips back to Display state.
* The saved view and the editor inputs must **never** appear at the same time.

---

## 3. Specific Screen Refactors

### 3.1. Housing Page: Tenancy & Master Lease (`frontend/src/pages/services/RentConfig.tsx`)
1. **When `config !== null` (Existing Saved Lease):**
   - Render a read-only **Master Tenancy Terms Card**:
     - **Monthly Rent:** `<Rupees paise={config.monthly_rent_paise} />` (Due Day: `{config.due_day_of_month}th of every month`)
     - **Contract Term:** `{formatDate(config.lease_start_date)} to {formatDate(config.lease_end_date)} ({config.duration_months} Months)` · Pill: `Month X of Y`
     - **Exit Protections:** Notice Period: `{config.notice_period_days} Days` · Lock-in: `{config.lock_in_months} Months`
     - **Escalation Clause:** `{config.escalation_percent}%` annual increment (Projected: `<Rupees paise={...} />`)
     - **Header Action:** `<button className={BUTTON_SECONDARY}>Edit Tenancy Terms</button>`
   - Render the read-only **Landlord Summary Card** (Name, Phone, UPI, PAN).
   - Render the **Completed Terms History** table.
2. **When user clicks "Edit Tenancy Terms":**
   - Open an **Edit Tenancy Terms Modal** (or toggle the card into in-place edit mode with `Cancel` and `Save Changes`).
   - The inputs (`MoneyInput`, `DateField`, `SelectField`, `NumberField`) only exist inside this edit state.
3. **When `config === null` (First-Time Setup):**
   - Render the initial setup form with a `"Set Up Lease"` button.

---

### 3.2. Rent Page: Rental Agreement Structure (`frontend/src/pages/services/Rent.tsx`)
1. **Read-Only Model Display:**
   - Instead of permanently displaying radio buttons and cards for all 5 models:
   - Render a concise card:
     - **Active Agreement Model:** `Single Master Lease (Equal Split)` (or `Room-Tiered Fixed Pricing`)
     - **Current Share Calculation:** `Equal share among all active participants (₹9,500/resident)`
     - **Receipt Preferences:** `Requires Landlord Receipts: Yes/No`
     - **Action:** `<button className={BUTTON_SECONDARY}>Change Agreement Model</button>`
2. **On "Change Agreement Model":**
   - Open a modal dialog or toggle in-place allowing the user to select another available model, with `Cancel` and `Save Model` actions.

---

### 3.3. Settings / Operator Setup (`frontend/src/pages/Setup.tsx`)
1. In the Operator / Settings sections:
   - Display saved settings (Default Apartment, Operator Name) as read-only key-value pairs with an **"Edit Settings"** button.
   - Only show input fields when actively editing or during initial setup.

---

## 4. Verification Checklist

1. **Visual & Interaction Check:**
   - Navigate to `/housing?tab=lease`: Confirm it opens to a clean, read-only contract card with an **"Edit Tenancy Terms"** button. No naked textboxes or dropdowns on load.
   - Click "Edit Tenancy Terms": Confirm the editor appears with `Cancel` and `Save Changes` buttons, and saving updates the card and returns to read-only mode.
   - Navigate to `/services/rent`: Confirm the Rental Agreement Structure is a clean display card with a "Change Model" button.
2. **Automated Tests:**
   - Update tests in `RentConfig.test.tsx`, `HousingHub.test.tsx`, `Rent.test.tsx`, and `Setup.test.tsx`.
   - Run vitest: `npm --prefix frontend test -- --run` and ensure all tests pass (223+ tests).
   - Run build: `npm --prefix frontend run build` to verify zero TypeScript errors.

