# Architectural Specification: In-Container Step-by-Step Tenancy Editor

**Role:** Lead Architect & UX Designer  
**Audience:** Claude Code (Lead Frontend Implementer)  
**Status:** Approved for Implementation  
**Governing Rule:** Rule U2 (Mutual Exclusion) & Container Footprint Preservation  
**Target File:** `frontend/src/pages/services/RentConfig.tsx` (and tests in `RentConfig.test.tsx`, `HousingHub.test.tsx`)

---

## 1. Domain & UX Rationale

When switching from the compact, read-only **Master Tenancy Terms** card into edit mode, the editor currently explodes into three separate `<Panel>` components stacked vertically (`Master Contract`, `Contract Term & Exit Protections`, `Renewal & Escalation Clause`). This multiplies the vertical height by 3x and violently shifts the page layout.

This specification refactors the editor into a **Single-Container Step-by-Step Sequential Editor**:
- It stays strictly within the **exact same container space** as the read-only card.
- The user edits one section at a time inside the same container.
- Step pills allow jumping directly between sections, while `Back` and `Next` buttons guide the flow.

---

## 2. Component Architecture (`frontend/src/pages/services/RentConfig.tsx`)

Refactor the edit state in `LeaseContractSection` / `LeaseForm`:

```
┌────────────────────────────────────────────────────────────────────────┐
│ 📄 Edit Master Tenancy Terms                        [Cancel]  [Save]   │
│                                                                        │
│   Step: [1. Contract]   [2. Term & Exit]   [3. Renewal & Escalation]   │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   (Active Step Inputs Rendered Here)                                   │
│                                                                        │
├────────────────────────────────────────────────────────────────────────┤
│ [← Back]                                              [Next: Term →]   │
└────────────────────────────────────────────────────────────────────────┘
```

### 2.1. State Machine
Add state for the active step:
```typescript
type TenancyStep = 'contract' | 'term' | 'escalation'
const [step, setStep] = useState<TenancyStep>('contract')
```

### 2.2. Single Container Layout
Instead of rendering three separate `<Panel>` components, render **one single `<Panel>`**:
- **Title:** `Edit Master Tenancy Terms`
- **Actions in Header:**
  - `<button type="button" className={BUTTON_SECONDARY} onClick={onCancel}>Cancel</button>`
  - `<button type="button" className={BUTTON_PRIMARY} onClick={onSave} disabled={!valid || save.isPending}>Save Changes</button>`

### 2.3. Step Navigation Bar
Render compact step indicator pills directly under the panel header:
```tsx
<div className="flex flex-wrap gap-2 pt-1 pb-3 border-b border-[var(--border-subtle)]" role="tablist" aria-label="Edit steps">
  {[
    { id: 'contract', label: '1. Contract' },
    { id: 'term', label: '2. Term & Exit' },
    { id: 'escalation', label: '3. Renewal & Escalation' },
  ].map((s) => (
    <button
      key={s.id}
      type="button"
      role="tab"
      aria-selected={step === s.id}
      onClick={() => setStep(s.id as TenancyStep)}
      className={clsx(
        'px-3 py-1 text-xs font-semibold rounded-full transition-colors cursor-pointer',
        step === s.id
          ? 'bg-[var(--accent)] text-white shadow-xs'
          : 'bg-[var(--bg-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
      )}
    >
      {s.label}
    </button>
  ))}
</div>
```

### 2.4. Step Body (One Section at a Time)
Renders strictly the active section:
1. **`step === 'contract'` (Contract Basics):**
   - Monthly Rent (`MoneyInput`)
   - Due Day of Month (`SelectField`)
2. **`step === 'term'` (Term & Exit Protections):**
   - Lease Start Date (`DateField`)
   - Agreement Duration (`SelectField`)
   - Notice Period (Days) (`NumberField`)
   - Lock-in Period (Months) (`NumberField`)
3. **`step === 'escalation'` (Renewal & Escalation):**
   - Escalation Method radio pills (Percentage / Fixed / None)
   - Renewal Escalation Rate (%)
   - Projected Renewal Rent box

### 2.5. Step Footer
At the bottom of the container:
```tsx
<div className="flex items-center justify-between pt-3 border-t border-[var(--border-subtle)]">
  {step !== 'contract' ? (
    <button
      type="button"
      className={BUTTON_SECONDARY}
      onClick={() => setStep(step === 'escalation' ? 'term' : 'contract')}
    >
      ← Back
    </button>
  ) : <div />}

  {step !== 'escalation' ? (
    <button
      type="button"
      className={BUTTON_SECONDARY}
      onClick={() => setStep(step === 'contract' ? 'term' : 'escalation')}
    >
      Next: {step === 'contract' ? 'Term & Exit' : 'Renewal'} →
    </button>
  ) : (
    <button
      type="button"
      className={BUTTON_PRIMARY}
      onClick={onSave}
      disabled={!valid || save.isPending}
    >
      Save Changes
    </button>
  )}
</div>
```

---

## 3. Verification Checklist

1. **Footprint & Height:**
   - Navigating to `/housing?tab=lease` and clicking `Edit` on the Master Tenancy Terms card:
   - Confirm it does **NOT** explode into three stacked panels.
   - Confirm it renders as a single unified container with the 3 step pills.
2. **Step Navigation:**
   - Clicking step pills jumps smoothly between Contract, Term, and Escalation.
   - Clicking `Next` advances the step; clicking `Back` returns to the previous step.
3. **Save & Cancel:**
   - Clicking `Cancel` or `Save Changes` flips smoothly back to the compact 4-quadrant read-only view.
4. **Automated Tests:**
   - Update tests in `RentConfig.test.tsx` and `HousingHub.test.tsx`.
   - Run vitest (`npm --prefix frontend test -- --run`) and verify all 234+ tests pass.

