# Technical Design Specification & Claude Code Handoff: Lease Contract Lifecycle, Notice Decision & Renewal Workflow

**Feature:** Lease Contract Terms, Notice Window Decision (Vacate vs. Renew), Escalation Matrix, and Deposit Recovery  
**Status:** Approved by User  
**Interactive Wireframe:** See `wireframe_lease_lifecycle.html` in artifact storage.  
**Role of this Document:** Implementation blueprint for Claude Code.

---

## 1. Executive Summary & Domain Invariants

In residential shared flats (particularly 11-month Indian agreements):
1. **Defined Contract Lifespan:** A lease has an explicit start date (`lease_start_date`), duration (`duration_months`, default 11), and end date (`lease_end_date`).
2. **The 30-Day Notice Window ("December Alert for January Expiry"):**
   * If a contract ends on 31 January and notice is 30 days, the notice window opens on **01 December**.
   * During this month, the system prompts the flatmates with an actionable choice:
     * **Path A: Plan to Vacate (Serve Notice)** — Initiates the move-out protocol, logs the notice date, and tracks security deposit recovery ($D_{\text{total}} - D_{\text{deductions}}$ split equally).
     * **Path B: Renew Tenure (+Escalation)** — Rolls the lease into the next 11-month contract with the agreed escalation rate.
3. **Escalation Matrix:**
   * Method: `Percentage Rate` (standard 5% or 10%), `Fixed Amount` (e.g. ₹30,000), or `No Escalation`.
   * Live projection: $\text{new\_rent} = \text{round}(\text{base\_rent} \times (1 + \text{rate}/100))$.
4. **Security Deposit Tracking:** Tracks total deposit held with owner and projected refund share per flatmate.
5. **Permissive Invariant:** A contract reaching expiry **never blocks** generating monthly rent. If negotiations are delayed, rent continues generating at the old rate with an inline reminder.

---

## 2. Database Migration (`aem/migrations/0006_lease_lifecycle.sql`)

```sql
-- Migration 0006: Lease Contract Term, Notice Window & Renewal History

ALTER TABLE rent_lease_config ADD COLUMN lease_start_date TEXT;      -- YYYY-MM-DD
ALTER TABLE rent_lease_config ADD COLUMN lease_end_date TEXT;        -- YYYY-MM-DD
ALTER TABLE rent_lease_config ADD COLUMN duration_months INTEGER DEFAULT 11 CHECK (duration_months > 0);
ALTER TABLE rent_lease_config ADD COLUMN notice_period_days INTEGER DEFAULT 30 CHECK (notice_period_days >= 0);
ALTER TABLE rent_lease_config ADD COLUMN lock_in_months INTEGER DEFAULT 6 CHECK (lock_in_months >= 0);
ALTER TABLE rent_lease_config ADD COLUMN security_deposit_paise INTEGER CHECK (security_deposit_paise IS NULL OR security_deposit_paise >= 0);
ALTER TABLE rent_lease_config ADD COLUMN escalation_type TEXT DEFAULT 'percent' CHECK (escalation_type IN ('percent', 'fixed', 'none'));
ALTER TABLE rent_lease_config ADD COLUMN escalation_percent REAL DEFAULT 5.0;
ALTER TABLE rent_lease_config ADD COLUMN renewal_rent_paise INTEGER;
ALTER TABLE rent_lease_config ADD COLUMN lease_status TEXT DEFAULT 'active' CHECK (lease_status IN ('active', 'notice_served', 'vacated', 'renewed'));
ALTER TABLE rent_lease_config ADD COLUMN notice_served_date TEXT;    -- YYYY-MM-DD
ALTER TABLE rent_lease_config ADD COLUMN target_handover_date TEXT;  -- YYYY-MM-DD

-- Historical audit of completed terms across multiple years
CREATE TABLE IF NOT EXISTS rent_lease_term (
    term_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id),
    term_number INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    monthly_rent_paise INTEGER NOT NULL,
    escalation_applied_percent REAL,
    archived_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

---

## 3. Backend Service Layer (`aem/services/rent.py`)

### 3.1 Extended Configuration Fields & Calculations
Update `_CONFIG_FIELDS` and `_clean_config` to accept and validate:
* `lease_start_date`: ISO date `YYYY-MM-DD`.
* `duration_months`: Integer (1–60), default 11.
* `lease_end_date`: If not provided, computed as `start_date + duration_months - 1 day`.
* `notice_period_days`: Integer, default 30.
* `lock_in_months`: Integer, default 6.
* `security_deposit_paise`: Integer paise.
* `escalation_type`: `'percent'`, `'fixed'`, or `'none'`.
* `escalation_percent`: Float/real (e.g. 5.0).
* `renewal_rent_paise`: Integer paise.

### 3.2 Timeline Status Helper: `get_lease_lifecycle_status(config, as_of_date=None)`
Returns computed status metadata:
* `term_number`: e.g. 1
* `current_month_index`: e.g. 10 (Month 10 of 11)
* `days_until_expiry`: e.g. 48
* `notice_deadline_date`: `lease_end_date - notice_period_days`
* `is_notice_window_active`: Boolean (`current_date >= notice_deadline_date` and `< lease_end_date`)
* `is_expired`: Boolean (`current_date > lease_end_date`)
* `projected_renewal_rent_paise`: Computed integer paise using escalation clause.

### 3.3 Lease Renewal Handler: `renew_lease_contract(db_path, apartment_id, payload)`
* Validates `new_start_date`, `new_duration_months`, `new_monthly_rent_paise`.
* Inserts current active term into `rent_lease_term` with `archived_at = now()`.
* Updates `rent_lease_config` with the new term dates and `monthly_rent_paise = new_monthly_rent_paise`.
* Resets `lease_status = 'active'`.

### 3.4 Notice to Vacate Handler: `serve_vacate_notice(db_path, apartment_id, payload)`
* Updates `rent_lease_config`:
  * `lease_status = 'notice_served'`
  * `notice_served_date = payload['notice_date']`
  * `target_handover_date = payload['target_handover_date']`

---

## 4. API Endpoints (`aem/api/routers/rent.py`)

- `POST /api/rent/lease/renew`:
  * Payload: `{ "new_start_date": "2027-02-01", "duration_months": 11, "new_monthly_rent_paise": 2992500, "escalation_type": "percent", "escalation_percent": 5.0 }`
  * Action: Rolls contract to Term 2, archives Term 1, returns updated config.
- `POST /api/rent/lease/vacate`:
  * Payload: `{ "notice_served_date": "2026-12-28", "target_handover_date": "2027-01-31", "notes": "30-day notice provided" }`
  * Action: Updates lease status to `notice_served`.

---

## 5. Frontend Touchpoints

### 5.1 `/rent/config` (`frontend/src/pages/RentConfig.tsx`)
1. **Contract Term & Exit Protections Panel (`CalendarClock` icon):**
   * Grid (2 columns, `items-start`):
     * Row 1: `Lease Start Date` (date input) | `Agreement Duration` (select: 11 mo, 12 mo, etc., displays auto-calculated End Date).
     * Row 2: `Notice Period (Days)` (default 30) | `Lock-in Period (Months)` (default 6).
     * Row 3: `Security Deposit Held by Landlord` (adorned prefix `₹` addon container, max-w-sm).
2. **Renewal & Escalation Clause Panel (`TrendingUp` icon):**
   * Escalation Method choice pills: `Percentage Rate` (default), `Fixed Amount`, `No Escalation`.
   * Input: `Renewal Escalation Rate (%)` with `%` suffix (or `Agreed Renewed Rent` with `₹` addon when Fixed is selected).
   * **Projected Renewal Rent Banner:**
     * Single unified callout showing: `Current ₹28,500.00 + 5.0% = ₹29,925.00 / month` with `~₹9,975.00 / person (+₹475)`.

### 5.2 `/rent` (`frontend/src/pages/Rent.tsx`)
1. **Header Term Pill:** `<Pill tone="warning">Term: Month 10 of 11 (Expires 28 Feb 2027)</Pill>`.
2. **Proactive Notice Window Decision Banner (when in notice window):**
   * Banner title: *"Notice Window Open: Master Lease Ends Next Month ([Date])"*.
   * Dual action buttons:
     * `[ 🚪 Plan to Vacate (Serve Notice) ]` $\rightarrow$ Opens Vacate Modal.
     * `[ ↻ Renew Tenure (+5% Escalation) ]` $\rightarrow$ Opens Renewal Modal.
3. **1-Click Renewal Modal (`Modal`):**
   * Side-by-side comparison (Expiring Term vs New Term).
   * Pre-filled new start date, duration, and auto-escalated rent (₹29,925.00).
   * Confirm button calls `POST /api/rent/lease/renew`.
4. **Plan to Vacate Modal (`Modal`):**
   * Notice date & handover date verification.
   * Security deposit recovery calculation: $D_{\text{total}} - D_{\text{deductions}} = \text{Net Refund}$ ($\text{₹45,000 each}$ for 3 roommates).
   * Confirm button calls `POST /api/rent/lease/vacate`.

### 5.3 `/services` (`frontend/src/pages/ServicesHub.tsx`)
* On the **Master Lease Rent** card, display:
  * Term progress bar: `11-Month Term (91% elapsed)`.
  * Pill: `Expires in 48d`.

---

## 6. Verification & Test Suite Checklist
1. **Backend Tests (`tests/test_rent.py`):**
   * Test migration 0006 applies cleanly.
   * Test saving lease with start date, duration, notice period, lock-in, and security deposit.
   * Test auto-calculation of `lease_end_date` and timeline status.
   * Test `POST /api/rent/lease/renew`: archives previous term in `rent_lease_term` and updates active config.
   * Test `POST /api/rent/lease/vacate`: updates status to `notice_served`.
   * Verify all existing 104 rent tests continue to pass.
2. **Frontend Tests:**
   * `RentConfig.test.tsx`: test rendering and saving contract term fields.
   * `Rent.test.tsx`: test notice banner visibility and modal triggers.
   * Run: `npm --prefix frontend test` and `npm --prefix frontend run typecheck`.

