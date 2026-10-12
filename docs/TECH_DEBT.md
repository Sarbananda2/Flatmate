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
│ Next Phase: Household Property Architecture (Floor Plan & Space Asset Model)  │
│   • Define physical Apartment asset boundaries                                │
│   • Structure rooms (Master, Common, Guest) & beds                            │
│   • Structure vehicle parking slots                                           │
└──────────────────────────────────────┬────────────────────────────────────────┘
                                       │
                                       ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│ Post-Household Phase: Space Allocation Linking (DEBT-006)                     │
│   • Link flatmates directly to rooms & bed slots within the household        │
│   • Connect physical rooms directly to `household_deposit_slot`               │
│   • Unlock Model 2 (Room-tiered rent pricing) & Model 3 (Sq.ft ratio)         │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## High-Priority Backlog Items

### DEBT-006: Space Allocation (Apartment Room & Bed Inventory)
- **Status:** Queued — **Scheduled immediately after the Household Property milestone** (which follows the 4 Flatmate Pillars).
- **Domain Context:** Space allocation (which flatmate sleeps in which room or bed, and which parking slot is assigned) must **not** be modeled as loose, unvalidated string labels directly on the `resident` table. 
- **Architectural Rationale:** The physical apartment floor plan (e.g. 3BHK with Master Bedroom + Balcony, Common Bedroom, 1 Car Parking Slot) outlives individual flatmates. Modeling rooms and beds as first-class physical assets under the `apartment` aggregate prevents double-occupancy conflicts, enables seamless room turnover, and serves as the prerequisite foundation for **Model 2 (Room-Tiered Rent)** and **Model 3 (Square-Footage Allocation)**.
- **Deposit Slot Bridge:** In Pillar B, the security deposit is structured as `household_deposit_slot` owned by the Household/Lease. When physical rooms are introduced, each deposit slot directly maps to `room_id` with zero restructuring required.
- **Prerequisite:** Completion of the Household Property domain definition.

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

