# Pillar D: Flatmate Statements & WhatsApp Sharing
## Architectural Specification & Technical Handoff for Claude Code

**Role:** Lead Architect & Financial Systems Designer  
**Audience:** Claude Code (Lead Backend & Frontend Implementer)  
**Status:** Approved for Implementation  
**Feature:** Individual Resident Monthly Statements, Itemized Breakdowns, One-Click WhatsApp Sharing, and Dynamic Scan-to-Pay UPI QR

---

## 1. Domain Context & The Core Problem

Currently, monthly statements in AEM are aggregated at the household level (`/statements`). When a statement is drafted or finalized at month-end, each individual flatmate asks:
1. *"What is my exact personal share for October?"*
2. *"Can I see the line-by-line itemization (Rent, Electricity, Wi-Fi, Maid, Groceries)?"*
3. *"Why is my electricity bill higher than others?"*
4. *"Can you send me a WhatsApp message with the UPI link/QR so I can settle immediately?"*

**Pillar D delivers an Individual Resident Monthly Statement module** that generates personal breakdowns for any settlement month, complete with:
- Itemized category lines (Rent, Electricity, Wi-Fi, Maid, Supplies).
- Payments and advances credited.
- Carry-over opening balances.
- Net monthly due payable to the household pool.
- A one-click **Copy WhatsApp Summary** button.
- A dynamic **Scan-to-Pay UPI QR Code** pre-filled with the exact net monthly due.
- A clean **Print / Save PDF** view.

---

## 2. Invariants & Financial Calculation

### Invariant D1 (Zero-Drift Personal Net Due):
For any resident $r$ in settlement month $m$:
$$\text{Net Due}_r = \text{Total Allocated Charges}_{r, m} - \text{Payments Applied}_{r, m} - \text{Advance Credits}_{r, m} + \text{Carried Over Opening Balance}_{r, m}$$

- **$\text{Net Due} > 0$:** Resident owes the household pool.
- **$\text{Net Due} < 0$:** Household pool owes the resident (e.g. resident paid a large grocery/electricity bill on behalf of the flat).
- **$\text{Net Due} = 0$:** Settled.

### Invariant D2 (Categorical Itemization):
Every charge line item must specify:
1. `category`: Rent, Electricity, Groceries, Wi-Fi, Maid, Other.
2. `description`: e.g. "Master Lease October Rent", "BESCOM Fixed + Room 2 AC", "Airtel Fiber".
3. `service_period`: e.g. "2026-09" for September electricity billed in October.
4. `allocation_method`: Equal split, Measured / Smart plug, Weighted, Custom.
5. `total_expense_amount_paise` vs `resident_share_paise`.

---

## 3. Backend Implementation

### 3.1 Schemas (`aem/schemas/models.py`)
Add typed models:
- `ResidentMonthlyStatementItem`:
  - `expense_id`: str
  - `category`: str
  - `description`: str
  - `expense_date`: str
  - `service_period`: str
  - `allocation_method`: str
  - `total_expense_paise`: int
  - `resident_share_paise`: int
  - `notes`: str | None
- `ResidentMonthlyPaymentItem`:
  - `payment_id`: str
  - `payment_date`: str
  - `payment_kind`: str
  - `payment_mode`: str
  - `amount_paise`: int
  - `reference`: str | None
- `ResidentMonthlyStatement`:
  - `resident_id`: str
  - `display_name`: str
  - `settlement_month`: str
  - `statement_status`: Literal['draft', 'finalized_correction_window', 'closed']
  - `items`: list[ResidentMonthlyStatementItem]
  - `total_charges_paise`: int
  - `payments`: list[ResidentMonthlyPaymentItem]
  - `total_payments_paise`: int
  - `net_due_paise`: int
  - `recipient_upi_id`: str | None (Primary signatory or flat pool UPI ID)
  - `recipient_name`: str | None
  - `whatsapp_text`: str (pre-formatted string ready for clipboard)

### 3.2 Service Layer (`aem/services/statements.py` or `aem/services/reports.py`)
Add `get_resident_monthly_statement(db, apartment_id, resident_id, settlement_month)`:
1. Validates apartment and resident existence.
2. Queries the statement status for `settlement_month`.
3. Fetches all allocations for `resident_id` in `settlement_month`, with expense definitions and components.
4. Fetches all payments applied towards those allocations, plus any direct pool payments in that month.
5. Resolves the recipient UPI ID (if Primary Signatory mode, signatory's UPI ID; else operator/apartment UPI ID).
6. Formats the pre-generated WhatsApp message string.

### 3.3 Router Endpoints (`aem/api/routers/residents.py` or `aem/api/routers/statements.py`)
- `GET /api/residents/{resident_id}/statements/{settlement_month}` $\rightarrow$ Returns `ResidentMonthlyStatement`.
- `GET /api/statements/{settlement_month}/flatmates` $\rightarrow$ Returns summary list of all flatmates' personal statements for that month for bulk review and sharing.

---

## 4. Frontend UI/UX Design

### 4.1 Feature 1: "Flatmate Breakdown & WhatsApp Sharing" on `/statements`
In `frontend/src/pages/Statements.tsx` (when viewing a specific settlement month):
- Render a dedicated section: **"Flatmate Shares & WhatsApp Dispatch"**.
- Displays a table with **center-aligned headers across all columns**:
  - Columns: `Resident` | `Month Charges` | `Paid / Credited` | `Net Due` | `Status` | `Actions`
  - Actions:
    - `[ Copy WhatsApp Summary ]` (Copies formatted WhatsApp message directly to clipboard with toast notification: *"WhatsApp summary copied for Bob"*).
    - `[ View Statement ]` (Opens modal with detailed slip & UPI QR).

### 4.2 Feature 2: Dedicated "Monthly Statement & Sharing" Card on `/residents/:id`
In `frontend/src/pages/ResidentDetail.tsx`:
- Render a new card: **"Monthly Statement & Sharing"**:
  - Month switcher: `< October 2026 >` (defaults to current settlement month).
  - Displays:
    - Itemized breakdown table of charges.
    - Payments applied.
    - Large Net Due callout badge (e.g. `₹3,450.00 Owed to Pool`).
  - Action row:
    - `[ Copy WhatsApp Breakdown ]`
    - `[ Scan to Pay UPI ]` (reveals dynamic QR code generated with `qrcode.react`)
    - `[ Print / Save PDF ]`

### 4.3 Pre-Formatted WhatsApp Text Format
```text
🏠 Flat 402 — October 2026 Statement
👤 Resident: Bob (Room 2)
──────────────────────────────
📋 Itemized Breakdown:
• Rent (Master Lease): ₹15,000.00
• Electricity (Fixed + Room 2): ₹1,450.00
• Wi-Fi (Airtel Fiber): ₹350.00
• Maid & Cook: ₹2,000.00
• Groceries & Supplies: ₹850.00
──────────────────────────────
Total Charges: ₹19,650.00
Payments Credited: -₹16,200.00
──────────────────────────────
👉 Net Due: ₹3,450.00 (Owed to Pool)

💳 Pay via UPI: sarbananda@okaxis
(Or scan QR code on the apartment dashboard)
```

---

## 5. Verification Commands

```bash
# Backend pytest suite
.venv/bin/pytest tests/test_resident_statements.py

# Frontend vitest suite and production build
npm --prefix frontend test
npm --prefix frontend run build
```

