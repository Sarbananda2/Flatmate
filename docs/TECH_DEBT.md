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

### DEBT-001: Single-Signatory Master Lease HRA Receipt Scrutiny
- **Category:** Legal & Tax Compliance
- **Current Behavior:** The system issues individual HRA rent receipts for all participating flatmates with the landlord's PAN.
- **Domain Issue:** In a Single Master Lease (Equal Split), the owner typically enters a contract with only one lead flatmate, or only accepts rent from a single bank account. Secondary flatmates lack direct privity of contract with the landlord, which can lead to employer HR audit queries during tax proof submission.
- **Future Resolution Path:**
  1. Add a lease signatory configuration: `joint_all_signatories` vs `single_anchor_signatory`.
  2. For single-signatory leases, generate an internal **Joint Tenancy / Flatmate Cost-Sharing Undertaking Annexure** for tax deduction compliance.
  3. Support Model 5 (Anchor Tenant / Sublet structure).

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

