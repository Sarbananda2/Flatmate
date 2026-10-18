# Architectural Specification: Setup Wizard Completion & Banner State Synchronization

**Role:** Lead Architect & Domain Modeler  
**Audience:** Claude Code (Lead Implementer)  
**Status:** Approved for Implementation  
**Governing Principles:** Cache Coherence across Client-Side Navigation, Real Single Source of Truth, No Phantom Warning Banners.  
**Files Affected:**
- `frontend/src/components/Layout.tsx`
- `frontend/src/pages/SetupWizard.tsx`
- `frontend/src/pages/SetupWizard.test.tsx`

---

## 1. Context & User Bug Report

### The Incident
The operator completed all 4 steps of the Guided Setup Wizard (`/welcome`):
1. **Apartment:** Flat 1805, CG6, Supertech Capetown.
2. **Residents:** Anagarag Mahanta, Arnab Deka, Sarbananda Taw.
3. **Categories:** Electricity, Maid, Cook, Cook essentials, Rent, Wifi.
4. **Step 4 (Ready / All Set):** The wizard displayed:
   - *"All set. The essentials are ready."*
   - Summary of all 3 residents and 6 categories.
   - Buttons: `[Add more people]`, `[Add more categories]`, `[Go to the dashboard]`.

Upon clicking `[Go to the dashboard]`, the browser navigated to `/`. At the top of the dashboard, a prominent banner appeared:
> **"Setup is half finished. Next: who lives here. Everything you entered is saved. [Pick up where you left off]"**

Yet directly below this banner, the dashboard rendered:
- "September 2026 Settlement Cycle"
- Resident Balances: `Anagarag Mahanta ₹0.00`, `Arnab Deka ₹0.00`, `Sarbananda Taw ₹0.00`.

---

## 2. Root Cause Analysis

### The Dual-Cache State Desynchronization
1. **`Layout` Component Lifecycle:**
   - In `App.tsx`, `<Layout>` wraps `<Routes>` at the top level. `<Layout>` **never unmounts** during client-side router navigation between `/welcome` and `/`.
   - In `Layout.tsx`:
     ```tsx
     const setup = useResource<SetupState>('/api/setup/state')
     ```
   - When the user first opened the application (or during early wizard steps), `Layout` executed this fetch once. At that point in time, residents had not yet been committed to the database, so `setup.data` in `Layout` was initialized to `{ next_step: 'residents', is_complete: false }`.
   - While on `/welcome`, `Layout` suppressed the banner because `onWizard` (`location.pathname === '/welcome'`) was `true`:
     ```tsx
     const onWizard = location.pathname === '/welcome'
     const setupIncomplete = Boolean(
       setup.data && !setup.data.is_complete && setup.data.next_step !== 'apartment',
     )
     {setupIncomplete && !onWizard && ( ... )}
     ```

2. **Isolated Hook State in `SetupWizardPage`:**
   - `SetupWizardPage` (`frontend/src/pages/SetupWizard.tsx`) maintains its **own independent** `useResource<SetupState>('/api/setup/state')`.
   - As the operator completed residents and categories, `SetupWizardPage` called its own local `state.reload()`. When setup completed, `SetupWizardPage`'s local state correctly showed `is_complete: true`.
   - However, **`SetupWizardPage` never notified `Layout`**.
   - In `finish` mutation:
     ```tsx
     onSuccess: () => {
       clearDraft()
       toast.success('Setup complete', 'You can change any of this later.')
       navigate('/')
     }
     ```
     `finish` did not call `reloadApartment()`.
   - In Step 4 (`data?.is_complete`):
     ```tsx
     <Link className="btn btn--primary" to="/">
       Go to the dashboard
     </Link>
     ```
     The link simply transitioned the URL to `/`.

3. **The Banner Appearance:**
   - When the operator clicked `Go to the dashboard`, `location.pathname` changed from `/welcome` to `/`.
   - `onWizard` became `false`.
   - `Layout`'s internal hook state was **never reloaded** and still retained the stale initial snapshot: `{ next_step: 'residents', is_complete: false }`.
   - Consequently, `setupIncomplete && !onWizard` evaluated to `true`, and line 450 rendered:
     `{setup.data?.next_step === 'residents' && 'Next: who lives here.'}`
   - If the operator performed a hard browser reload (F5) on `/`, `Layout` remounted, fetched fresh data (`is_complete: true`), and the banner vanished.

---

## 3. Required Implementation Architecture

### 3.1. Automatic Route-Change & Exit Invalidation in `Layout.tsx`
In `frontend/src/components/Layout.tsx`:
1. When leaving `/welcome` (or whenever `location.pathname` transitions from `/welcome` to any other page), `Layout` must immediately refresh `setup.reload()` and `apartments.reload()`.
2. Additionally, track previous path or trigger reload when transitioning off `/welcome`:
```tsx
const prevPathRef = useRef(location.pathname)

useEffect(() => {
  if (prevPathRef.current === '/welcome' && location.pathname !== '/welcome') {
    setup.reload()
    apartments.reload()
  }
  prevPathRef.current = location.pathname
}, [location.pathname, setup.reload, apartments.reload])
```

### 3.2. Explicit Context Notification in `SetupWizard.tsx`
In `frontend/src/pages/SetupWizard.tsx`:
1. Consume `reloadApartment` from `useApp()`:
```tsx
const { reloadApartment } = useApp()
```
2. In the `finish` mutation's `onSuccess` callback, call `reloadApartment()`:
```tsx
const finish = useMutation(
  () => api.post<{ statement_id: string | null }>('/api/setup/finish', {
    create_statement_for: draft.createStatement ? state.data?.current_month : null,
  }),
  {
    onSuccess: () => {
      clearDraft()
      reloadApartment()
      toast.success('Setup complete', 'You can change any of this later.')
      navigate('/')
    },
  },
)
```
3. In the Step 4 completed view (`data?.is_complete`), ensure clicking `Go to the dashboard` triggers `reloadApartment()`:
```tsx
<Link
  className="btn btn--primary"
  to="/"
  onClick={() => reloadApartment()}
>
  Go to the dashboard
</Link>
```

### 3.3. Apartment Parameter Synchronization in `Layout.tsx`
In `Layout.tsx`:
Ensure `setup` query is passed the preferred `apartment_id` if available, or reloaded whenever `apartment` changes:
```tsx
const preferredApartmentId = apartments.data?.default_apartment_id ?? null
const setup = useResource<SetupState>(
  '/api/setup/state',
  preferredApartmentId ? { apartment_id: preferredApartmentId } : undefined,
  [preferredApartmentId]
)
```

---

## 4. Verification & Testing Requirements

1. **Vitest Unit & Integration Tests (`SetupWizard.test.tsx`):**
   - Verify that clicking "Finish setup" triggers `reloadApartment()`.
   - Verify that clicking "Go to the dashboard" from the completed screen triggers `reloadApartment()`.
   - Verify that navigating from `/welcome` to `/` clears any stale incomplete setup banner.
2. **Regression Check:**
   - Existing setup wizard step progression (apartment -> residents -> categories -> ready) continues to function seamlessly.
   - Reopening setup via `/welcome` or Settings still correctly displays "All set" when setup is completed on the server.

