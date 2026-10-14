# Architectural Specification: Dedicated Housing Management Hub (`/housing`)

**Role:** Lead Architect & UX Designer  
**Audience:** Claude Code (Lead Frontend & Backend Implementer)  
**Status:** Approved for Implementation  
**Target Routes:** `/housing` (Top-Level Ribbon Destination)  
**Key Files Affected:**
- `frontend/src/components/Layout.tsx` (Top-level Navigation Ribbon)
- `frontend/src/App.tsx` (Routing & Redirects)
- `frontend/src/pages/housing/HousingHub.tsx` (New Master Container)
- `frontend/src/pages/services/ServicesHub.tsx` (Link update)
- Tests: `frontend/src/pages/housing/HousingHub.test.tsx`

---

## 1. Domain Context & Objective

Currently, housing assets and legal contracts are fragmented across multiple sub-pages (`/services/rent/config`, `/services/deposits`, `/services/property`), while `/services` mixes long-term physical property configuration with monthly execution cycles (rent disbursements, electricity meter readings).

This milestone elevates **Housing** into a top-level first-class domain in the master navigation ribbon, providing a single, unified home for all physical, financial, and legal configurations of the apartment.

---

## 2. Navigation Ribbon Hierarchy (`Layout.tsx`)

Update `NAV_TABS` in `frontend/src/components/Layout.tsx`:

```typescript
const NAV_TABS: Array<{ to: string; label: string; section: Section }> = [
  { to: '/', label: 'Month Hub', section: 'hub' },
  { to: '/housing', label: 'Housing', section: 'housing' },
  { to: '/services', label: 'Services & Utilities', section: 'services' },
  { to: '/residents', label: 'Residents', section: 'residents' },
  { to: '/statements', label: 'Statements', section: 'statements' },
  { to: '/transactions', label: 'Activity Ledger', section: 'ledger' },
]
```

Add `'housing'` to `type Section = 'hub' | 'housing' | 'services' | 'ledger' | 'residents' | 'statements' | 'settings'`.

Map section prefixes:
- `['/housing', 'housing']`
- `['/property', 'housing']`
- `['/deposits', 'housing']`

---

## 3. Dedicated Housing Page Architecture (`/housing`)

Create `frontend/src/pages/housing/HousingHub.tsx` (exported as default and named component).

### 3.1. Executive Summary Strip
At the top of the page, render a concise summary banner with high-level badges:
- **Apartment Identity:** Name & Address (e.g., `Flat 402, Green Glen Layout`).
- **Physical Space:** `X Rooms · Y Occupied · Z Vacant`.
- **Active Lease:** `Monthly Rent: ₹X` · `Term: Month M of N` · `Expires: Date`.
- **Security Deposit:** `₹X Escrow` · `Funding Mode: Primary Signatory / Co-Tenants` · `Balanced / Unbalanced status`.
- **Landlord / Owner:** Name, Phone, and UPI ID shortcut.

### 3.2. Tabbed Sub-Views (`?tab=...`)
Synchronize active tab with the URL query parameter `?tab=lease|deposits|rooms|contacts|documents`. Default tab: `lease`.

The 5 tabs embed existing, tested sub-pages/components:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  🏠  Household & Housing Management                                                    │
│  Central configuration for lease contracts, security deposits, physical spaces & docs  │
├────────────────────────────────────────────────────────────────────────────────────────┤
│  [1. Lease & Contract]  [2. Security Deposit]  [3. Rooms]  [4. Contacts]  [5. Documents]│
└────────────────────────────────────────────────────────────────────────────────────────┘
```

1. **Tab 1: Lease & Contract (`tab=lease`):**
   - Render the lease contract settings and term lifecycle view (from `frontend/src/pages/services/RentConfig.tsx`).
   - Fields: Monthly rent (₹28,500), due date, start/end dates, notice period, lock-in, escalation clause, landlord bank/PAN/UPI, renewal history.
2. **Tab 2: Security Deposit Escrow (`tab=deposits`):**
   - Render the deposit escrow view (from `frontend/src/pages/services/SecurityDeposits.tsx`).
   - Master deposit (₹1,50,000), funding mode, signatory capital exposure, deposit slots table linked to rooms.
3. **Tab 3: Rooms Inventory (`tab=rooms`):**
   - Render the physical room inventory view (from `frontend/src/pages/services/PropertyRooms.tsx`).
   - Rooms table: Room Number, Washroom, Balcony, Occupant, Linked Deposit Slot, Actions.
4. **Tab 4: Stakeholders & Contacts (`tab=contacts`):**
   - Render the stakeholders directory (from `frontend/src/pages/services/PropertyContacts.tsx`).
   - Cards for Owner, Broker, Caretaker, Society Manager, Emergency lines with Call, WhatsApp, Mail shortcuts.
5. **Tab 5: Document Vault (`tab=documents`):**
   - Render the cryptographic document vault (from `frontend/src/pages/services/DocumentVault.tsx`).
   - Document table with SHA-256 validation, inline preview, download, and drag-and-drop upload.

---

## 4. Routing & Backward Compatibility (`App.tsx`)

In `frontend/src/App.tsx`:
1. Add route:
   `<Route path="/housing" element={<HousingHubPage />} />`
2. Add backward-compatible redirects to preserve existing bookmarks and deep links:
   - `<Route path="/services/rent/config" element={<Navigate to="/housing?tab=lease" replace />} />`
   - `<Route path="/rent/config" element={<Navigate to="/housing?tab=lease" replace />} />`
   - `<Route path="/services/deposits" element={<Navigate to="/housing?tab=deposits" replace />} />`
   - `<Route path="/deposits" element={<Navigate to="/housing?tab=deposits" replace />} />`
   - `<Route path="/services/property" element={<Navigate to="/housing?tab=rooms" replace />} />`
   - `<Route path="/property" element={<Navigate to="/housing?tab=rooms" replace />} />`
   - `<Route path="/services/rooms" element={<Navigate to="/housing?tab=rooms" replace />} />`
   - `<Route path="/rooms" element={<Navigate to="/housing?tab=rooms" replace />} />`

---

## 5. Updates to Services Hub (`ServicesHub.tsx`)

In `frontend/src/pages/services/ServicesHub.tsx`:
1. Update the **Household Property** card:
   - Change heading / badge to **"Housing Configuration"**.
   - Button: **"Manage Housing →"** linking to `/housing`.
   - Displays summary pills: Lease Active, Deposit Funded, 3 Rooms, 3 Documents on file.
2. The remaining cards on `/services` focus strictly on operational services:
   - **Rent Cycle** (`/services/rent`)
   - **Electricity Bills** (`/services/electricity`)
   - Planned service cards (Broadband, Maid/Cook, LPG, Water).

---

## 6. Design System Rules & Constraints

1. **Table Header Center-Alignment:** Ensure every data table across all 5 tabs continues to have center-aligned column headers (`<th className="... text-center">`).
2. **Client-Side Form Validation:** All modal and inline forms must validate non-empty text, numeric ranges, phone/email/PAN/IFSC syntax before submission.
3. **Month Context Independence:** The Housing configuration page represents persistent asset state and does not filter by month (month picker hidden on `/housing`).

---

## 7. Verification Checklist

1. **Frontend Tests:**
   - Create `HousingHub.test.tsx` verifying tab switching, query string persistence, and child view rendering.
   - Run vitest (`npm --prefix frontend test -- --run`) and verify all tests pass (215+ tests).
2. **Build Verification:**
   - Run `npm --prefix frontend run build` to confirm zero TypeScript or bundling errors.
3. **Live UI Verification:**
   - Confirm "Housing" appears on the top navigation bar.
   - Clicking through all 5 tabs (`?tab=lease`, `?tab=deposits`, `?tab=rooms`, `?tab=contacts`, `?tab=documents`) loads the corresponding views smoothly.
   - Test that visiting `/services/deposits` or `/property` immediately redirects to the right tab in `/housing`.

