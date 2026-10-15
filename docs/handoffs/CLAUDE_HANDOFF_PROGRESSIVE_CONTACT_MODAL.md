# Architectural Specification: Progressive Stepped Contact Modal

**Role:** Lead Architect & UX Designer  
**Audience:** Claude Code (Lead Frontend Implementer)  
**Status:** Approved for Implementation  
**Governing Rule:** Rule U2 (Mutual Exclusion) & Progressive Disclosure Standard  
**Target File:** `frontend/src/pages/services/PropertyContacts.tsx` (and tests in `PropertyContacts.test.tsx`)

---

## 1. Domain Context & Problem Statement

Currently, `ContactModal` renders up to 10 stacked vertical input fields in a single continuous `<form>`, creating an overwhelming form with a tall vertical scrollbar. 

This specification converts `ContactModal` into a **Role-Aware Progressive Stepper**:
- The form is split into 2–3 bite-sized logical steps based on the contact's role.
- Step indicator pills at the top allow quick jumping between steps.
- Footer buttons provide clear `Back`, `Next`, and `Save Contact` navigation.
- Zero vertical scrolling: the modal stays within a fixed, comfortable dialog height.

---

## 2. Step Breakdown by Contact Role

The number and labels of steps adapt dynamically to the selected `role`:

### Case A: Role = `owner` (Property Owner) — 3 Steps
1. **Step 1: Contact Identity** (`contact`)
   - Role selector (`role`)
   - Full Name * (`name`)
   - Phone Number (`phone`)
   - Email Address (`email`)
2. **Step 2: Tax Identity (HRA)** (`tax`)
   - PAN Card Number (`pan`)
   - Hint: *"Required for flatmates claiming HRA tax exemption on employer receipts."*
3. **Step 3: Payout & Banking** (`payout`)
   - Bank Account Number (`bank_account_no`)
   - Bank IFSC (`bank_ifsc`)
   - UPI ID (`upi_id`)
   - Additional Notes (`notes`)

### Case B: Role = `broker` — 3 Steps
1. **Step 1: Contact Identity** (`contact`)
   - Role, Name, Phone, Email
2. **Step 2: Brokerage & Agency** (`agency`)
   - Agency Name (`agency_name`)
   - Brokerage Fee Amount (`fee_amount_paise`)
   - Fee Status (`fee_status`: `pending`, `paid`, `na`)
3. **Step 3: Payout & Notes** (`payout`)
   - UPI ID (`upi_id`)
   - Additional Notes (`notes`)

### Case C: Role = `caretaker` | `society_manager` | `emergency_utility` — 2 Steps
1. **Step 1: Contact Identity** (`contact`)
   - Role, Name, Phone, Email
2. **Step 2: Duties & Payout** (`duties`)
   - Responsibilities (`responsibilities`) — e.g. *"Plumbing, gate passes, society NOCs"*
   - UPI ID (`upi_id`) (if applicable)
   - Additional Notes (`notes`)

---

## 3. UI & Interaction Specification

### 3.1. Step Navigation Pills
Render compact step indicator pills directly under the modal header:
- Active step: highlighted with `bg-[var(--accent)] text-white shadow-xs`.
- Inactive step: subtle pill `border border-[var(--border-medium)]`.
- Error indicator: If an off-step field contains an error that blocks saving, display a small red dot on its step pill (consistent with the Tenancy Stepper).

### 3.2. Modal Footer Navigation
- **Step 1:**
  - Left: `<button className={BUTTON_SECONDARY} onClick={onClose}>Cancel</button>`
  - Right: `<button className={BUTTON_PRIMARY} onClick={() => setStep(step2)}>Next →</button>`
- **Intermediate Steps:**
  - Left: `<button className={BUTTON_SECONDARY} onClick={() => setStep(stepPrev)}>← Back</button>`
  - Right: `<button className={BUTTON_PRIMARY} onClick={() => setStep(stepNext)}>Next →</button>`
- **Final Step:**
  - Left: `<button className={BUTTON_SECONDARY} onClick={() => setStep(stepPrev)}>← Back</button>`
  - Right: `<button type="submit" className={BUTTON_PRIMARY} disabled={!valid || save.pending}>{save.pending ? 'Saving…' : contact ? 'Save Contact' : 'Add Contact'}</button>`
- Header action: Optional direct `Save Contact` button enabled whenever all required fields are valid.

---

## 4. Verification Checklist

1. **Owner Contact Flow:**
   - Open edit on an Owner (e.g., Kumar Yadav):
     - Starts on Step 1: Role, Name, Phone, Email.
     - Click Next $\rightarrow$ Step 2: PAN input with HRA hint.
     - Click Next $\rightarrow$ Step 3: Account, IFSC, UPI, and Notes.
     - Confirm zero vertical scrollbar is present.
2. **Broker & Caretaker Flow:**
   - Switching Role to Broker updates step 2 to "Agency & Fee".
   - Switching Role to Caretaker switches to 2 steps ("Identity" and "Duties & Notes").
3. **Validation & Persistence:**
   - Typing an invalid PAN or IFSC highlights the error and flags the step pill.
   - Saving persists correctly to `PATCH /api/property/contacts/{contact_id}`.
4. **Automated Tests:**
   - Update `PropertyContacts.test.tsx` to step through the progressive modal.
   - Run vitest: `npm --prefix frontend test -- --run` and verify all tests pass.

