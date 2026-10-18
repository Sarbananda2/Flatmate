# Technical & Architectural Debt Register

This register documents architectural trade-offs, deferred features, and legal/domain gaps tracked for upcoming development iterations.

---

## Roadmap Dependency Sequence

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ Phase 1: Flatmate Management (4 Pillars) [COMPLETED]                          │
│   • [x] Pillar A: Profile, Contact Card & Dynamic UPI QR                      │
│   • [x] Pillar B: Household Security Deposit Slots & Sub-Escrow Tracking      │
│   • [x] Pillar C: Guided Exit & Settlement Clearance Workflow                 │
│   • [x] Pillar D: Flatmate Statements & WhatsApp Sharing                      │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                       │
                                       ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ Phase 2: Household Property Architecture [COMPLETED]                          │
│   • [x] Room inventory (washroom, balcony, single-occupancy, move-out release)│
│   • [x] Stakeholders & Contacts (Owner/PAN/Bank, Broker/Fees, Caretaker)      │
│   • [x] Tamper-Proof Document Vault (PostgreSQL BYTEA + SHA-256 validation)   │
│   • [x] Unified Property Hub (/services/property with 3 tabbed views)         │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                       │
                                       ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ Post-Household Phase: Space Allocation Linking (DEBT-006) [COMPLETED]         │
│   • [x] Link flatmates directly to rooms & bed slots within the household    │
│   • [x] Connect physical rooms directly to `household_deposit_slot`           │
│   • [x] Unlock Model 2 (Room-tiered rent pricing) & base rent attributes      │
│   • [x] Reconcile / Synchronize Owner Contact with Lease Landlord details     │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                       │
                                       ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ Upcoming Milestones & Backlog Options                                         │
│   • DEBT-001: Single-Signatory Master Lease HRA Receipt Scrutiny              │
│   • DEBT-002: Move-In / Move-Out Daily Proration Policy                       │
│   • DEBT-004: Vehicle & Parking Slot Allocation                               │
│   • DEBT-007: Explicit Shortfall Float / Bridge Advance (Rent Arrears)        │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## High-Priority Backlog Items

### DEBT-006: Space Allocation (Apartment Room & Bed Inventory)
- **Status:** [COMPLETED]
- **Resolution:**
  - `household_deposit_slot.room_id` linked and displayed across Rooms Inventory (`/services/property`) and Security Deposits (`/services/deposits`).
  - Migration 0012 added `base_rent_paise` to `household_room` and enforced unique room-slot pairs.
  - Rent generation engine updated to support `split_mode: "room_tiered"` alongside `"equal"`.
  - Bi-directional synchronization between `household_contact` (Owner) and `rent_lease_config` established.

---

### DEBT-001: HRA Rent Receipt Generation & Tax Proof Scrutiny
- **Category:** Legal & Tax Compliance / Domain Simplification
- **Status:** [FEATURE DISABLED ACROSS APP]
- **Previous Behavior:** The system automatically generated and displayed individual HRA rent receipts for all participating flatmates with the landlord's PAN upon landlord payout.
- **Domain Issues & Decision:**
  1. *Master Lease Privity:* In a Single Master Lease (Equal Split), the owner typically enters a contract with only one anchor flatmate. Secondary flatmates lack privity of contract with the landlord, leading to rejection or queries during employer HR tax audit proof submissions.
  2. *Tranche Disbursement Incompatibility:* Under the Tranche / Split Landlord Disbursement model (Approach 3), rent is paid in multiple tranches (e.g. 5th and 7th), making atomic HRA receipt generation for late or partial payers premature and legally ambiguous.
  3. *Operational Focus:* The application prioritizes operational cash flow: tracking who paid into the House Kitty pool, tracking tranches sent to the landlord with bank UTRs, and attaching the landlord's actual signed/stamped receipt.
- **Action Taken:** HRA receipt generation, HRA slip modal triggers, and "View HRA Slip" buttons are disabled across the application.
- **Future Resolution Path (When Re-enabling):**
  1. Add a lease signatory configuration: `joint_all_signatories` vs `single_anchor_signatory`.
  2. For single-signatory leases, generate an internal **Joint Tenancy / Flatmate Cost-Sharing Undertaking Annexure** compliant with Indian income tax norms.
  3. Support Model 5 (Anchor Tenant / Sublet structure) and tie receipt generation strictly to verified full-settlement cycles.

---

### DEBT-002: Move-In / Move-Out Daily Proration
- **Category:** Accounting & Allocations
- **Current Behavior:** Strict *"Nothing prorates"* rule. Flatmates moving in or out mid-month pay either a full equal share or are explicitly excluded.
- **Domain Issue:** In co-living setups with mid-month resident turnover, flatmates frequently negotiate daily-rated shares (e.g. leaving on the 10th). Currently requires manual offline adjustments.
- **Future Resolution Path:** Configurable proration policies (`none` | `daily_30` | `daily_exact`).

---

### DEBT-003: Emergency Contact Profile Fields
- **Category:** Resident Safety & KYC
- **Resolution:** Being implemented in **Pillar A** (Next-of-kin Name, Phone, and Relationship).

---

### DEBT-004: Vehicle & Parking Slot Allocation
- **Category:** Property Management
- **Resolution:** Scheduled as part of the **Household Property** modeling phase.

---

### DEBT-007: Explicit Shortfall Float / Bridge Advance (Rent Arrears)
- **Category:** Accounting & Cash Flow / Rent Management
- **Current Behavior:** System assumes rent to the landlord is funded either entirely from the pooled house kitty or by a single resident advancing the entire month's rent. There is no first-class intra-resident bridge loan mechanism when an admin or specific flatmate temporarily covers a delinquent flatmate's share during a full payout.
- **Context:** In Approach 2 research, we evaluated an explicit bridge advance model where a resident who floats the missing share (e.g. ₹15,000 for Tenant C on the 5th) is credited an internal bridge float, which automatically reimburses them when Tenant C settles on the 7th.
- **Decision:** Deferred to technical debt. The household prioritized **Approach 3 (Tranche / Split Landlord Disbursement)** to disburse collected funds in tranches as they arrive directly to the landlord.
- **Future Resolution Path:** If single-transfer full payouts are mandated without a pooled buffer, introduce an internal `shortfall_advance` entity that links delinquent allocation shares to the funding resident and automatically routes late payments to settle the advance.

