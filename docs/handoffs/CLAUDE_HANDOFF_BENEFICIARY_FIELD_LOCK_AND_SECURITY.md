# Architectural Specification: Payout Beneficiary Locked Input & Backend Security Enforcement

**Role:** Lead Architect & Domain Modeler  
**Audience:** Claude Code (Lead Backend & Frontend Implementer)  
**Status:** Approved by User for Implementation  
**Governing Standards:** Rule D1 (Field Atomicity), Rule U1 (Unified Presentation), and Defense-in-Depth Security.  
**Entities Covered:** `household_contact` and `rent_lease_config` (`PayoutBeneficiaryField`, `beneficiary_name`, `payout_beneficiary_name`).

---

## 1. Context & User Intent

In the previous iteration, `PayoutBeneficiaryField` rendered the `TextField` only when `custom === true` ("Custom Beneficiary"), and hid it when `custom === false` ("Same as Landlord").

The user requested two explicit improvements:
1. **Always keep the Beneficiary Name field visible in both states:**
   - In **"Same as Landlord"**: The field remains visible, displays the landlord's full legal name, and is **LOCKED** (`disabled={true}`) so it cannot be altered.
   - In **"Custom Beneficiary"**: The field is **UNLOCKED** (`disabled={false}`), editable, and required.
2. **Backend Enforcement & Anti-SQL Injection Defense:**
   - The lock must be enforced on the backend: if the intent is "Same as Landlord", the backend strictly sets the beneficiary to `None` regardless of client tampering.
   - Strict pattern validation on custom beneficiary names to disallow SQL injection payloads (`;`, `--`, comments, control characters) or script tags.
   - Complete verification that all database operations use 100% parameterized queries (`%s`).

---

## 2. Frontend UI Implementation

### 2.1. Update `PayoutBeneficiaryField` (`frontend/src/components/Form.tsx`)

Modify `PayoutBeneficiaryField`:
1. Always render the `TextField` below the 2-option radio group.
2. When `!custom` ("Same as Landlord"):
   - `value`: `landlordName` (dynamically pulled from Tab 1 or the lease record).
   - `disabled`: `true` (locked input).
   - `required`: `false` (does not block form submission).
   - `hint`: `"🔒 Locked to the landlord's legal name. Switch to 'Custom Beneficiary' above to edit."`
   - `error`: `null`.
3. When `custom` ("Custom Beneficiary"):
   - `value`: `value` (the custom input string).
   - `disabled`: `false` (unlocked & editable).
   - `required`: `true`.
   - `placeholder`: `"e.g. Anita Sharma"`
   - `hint`: `"Whose bank account or UPI ID receives payouts (e.g., spouse, parent, family business)."`
   - `error`: standard validation error when submitted empty.

```tsx
export function PayoutBeneficiaryField({
  landlordName,
  custom,
  onCustomChange,
  value,
  onChange,
  error,
}: {
  landlordName: string
  custom: boolean
  onCustomChange: (custom: boolean) => void
  value: string
  onChange: (value: string) => void
  error?: string | null
}) {
  const labelId = useId()
  const choice = (selected: boolean) =>
    clsx(
      'px-3 py-1.5 text-xs font-semibold rounded-md text-left transition-colors cursor-pointer',
      selected
        ? 'bg-[var(--accent)] text-white shadow-xs'
        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
    )

  return (
    <div className="field">
      <span className="field-label" id={labelId}>
        Payout Beneficiary
      </span>
      <div
        role="radiogroup"
        aria-labelledby={labelId}
        className="inline-flex flex-wrap justify-self-start max-w-full p-1 gap-1 rounded-lg border border-[var(--border-medium)] bg-[var(--bg-elevated)]"
      >
        <button
          type="button"
          role="radio"
          aria-checked={!custom}
          onClick={() => onCustomChange(false)}
          className={choice(!custom)}
        >
          Same as Landlord ({landlordName})
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={custom}
          onClick={() => onCustomChange(true)}
          className={choice(custom)}
        >
          Custom Beneficiary
        </button>
      </div>

      <div className="mt-1.5">
        <TextField
          label="Beneficiary Name"
          value={custom ? value : landlordName}
          onChange={onChange}
          disabled={!custom}
          required={custom}
          maxLength={200}
          placeholder="e.g. Anita Sharma"
          hint={
            custom
              ? 'Whose bank account or UPI ID receives payouts (e.g., spouse, parent, family business).'
              : "🔒 Locked to the landlord's legal name. Switch to 'Custom Beneficiary' above to edit."
          }
          error={custom ? error : null}
        />
      </div>
    </div>
  )
}
```

---

## 3. Backend Implementation & Security Enforcement

### 3.1. Strict Name Validation & Invariant Lock (`aem/services/property.py`)

Update `beneficiary_name(...)` in `aem/services/property.py`:
1. **Allowed Characters Pattern:**
   Custom names must strictly contain only valid human or entity name characters (letters, numbers, spaces, dots, commas, hyphens, apostrophes, ampersands). Disallow SQL injection characters (`;`, `--`, `/*`, quotes `'`, `"`, etc.) and HTML tags.
   ```python
   _BENEFICIARY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9\s.,'&-]{1,200}$")
   ```
2. **Lock Invariant:**
   - If `value` is `None`, empty string, or whitespace $\rightarrow$ return `None`.
   - If `value` matches `owner_name` (case-insensitive trimmed) $\rightarrow$ return `None` (lock to owner).
   - If `value` fails `_BENEFICIARY_NAME_PATTERN` $\rightarrow$ raise `ValidationError("Beneficiary name contains invalid characters.", details={"field": field})`.
3. Implementation:
   ```python
   def beneficiary_name(value: Any, owner_name: str | None,
                        field: str = "beneficiary_name") -> str | None:
       """Whose name the owner's payout account is in, when it is not the owner's
       own. Naming the owner themself is the same as naming nobody."""
       name = optional_text(value, field, max_length=200)
       if not name:
           return None
       if owner_name and name.casefold() == owner_name.strip().casefold():
           return None
       if not _BENEFICIARY_NAME_PATTERN.match(name):
           raise ValidationError(
               "Beneficiary name contains invalid characters.",
               details={"field": field}
           )
       return name
   ```

### 3.2. Parameterized SQL Verification
Confirm that [`aem/persistence/repositories/property.py`](file:///home/sarbanandataw/Projects/apartment_expense_manager_prototype/aem/persistence/repositories/property.py) and [`rent.py`](file:///home/sarbanandataw/Projects/apartment_expense_manager_prototype/aem/persistence/repositories/rent.py) pass `beneficiary_name` and `payout_beneficiary_name` strictly through parameterized `%s` placeholders.

---

## 4. Verification Checklist

1. **Backend Tests:**
   - In `tests/test_property.py`:
     - Test that saving a valid custom beneficiary (e.g. `"Anita Sharma"`, `"Sharma & Sons, Co."`) succeeds.
     - Test that attempting an injection payload (e.g., `"'; DROP TABLE household_contact; --"`, `"<script>alert(1)</script>"`) raises `ValidationError` with 422.
     - Test that submitting the landlord's own name normalizes to `None`.
   - Run:
     ```bash
     .venv/bin/pytest tests/test_property.py tests/test_rent.py
     ```
2. **Frontend Tests:**
   - In `frontend/src/pages/services/PropertyContacts.test.tsx` and `frontend/src/pages/Payments.test.tsx`:
     - Verify that in "Same as Landlord" mode, the Beneficiary Name input is rendered, disabled, and displays the landlord's name.
     - Verify that in "Custom Beneficiary" mode, the Beneficiary Name input is enabled and editable.
   - Run:
     ```bash
     npm --prefix frontend test -- --run
     ```
3. **Live UI Check:**
   - Open `/housing?tab=contacts` or `/payments` on port 8787.
   - Open Landlord Profile or Landlord Account modal:
     - When "Same as Landlord" is active, observe the Beneficiary Name input locked with the landlord's name and lock hint.
     - Click "Custom Beneficiary", observe the field unlock for editing.

