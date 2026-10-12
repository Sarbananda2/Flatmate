# Data model

Sixteen tables, twenty indexes, fifteen integrity triggers. Every money column
is `INTEGER` paise; every timestamp is ISO-8601 with a timezone; every date is
`YYYY-MM-DD` and every period is `YYYY-MM`, both compared as strings in SQL.

Written for: engineers reading or extending the schema.

Generate the live schema at any time with:

```bash
sqlite3 data/apartment.sqlite .schema
```

---

## Relationships

```
apartment ─┬─ resident ──────────────┬── allocation ── payment_application
           │                         │
           ├─ expense_definition ─┐  ├── payment ──────────┘
           │                      │  │      └─ expense_id (funding link)
           ├─ monthly_statement ──┼──┤
           │     └ statement_revision │
           │                      │  └── ledger_adjustment
           └─ expense_instance ◀──┘
                 ├─ expense_component ── allocation
                 ├─ smart_plug_reading
                 └─ electricity_bill

app_setting, audit_log, schema_migration stand alone.
```

---

## Core records

### `apartment`

The container for everything. A household normally has one.

| Column | Type | Notes |
| --- | --- | --- |
| `apartment_id` | TEXT PK | Short operator-chosen key |
| `name` | TEXT | |
| `currency_code` | TEXT | `INR` by default |
| `timezone` | TEXT | `Asia/Kolkata` by default |
| `created_at` | TEXT | |

### `resident`

Never deleted. Moving out sets `is_active = 0` and `active_until`; every
financial record stays exactly where it was.

| Column | Type | Notes |
| --- | --- | --- |
| `resident_id` | TEXT PK | |
| `apartment_id` | TEXT FK → apartment | |
| `display_name` | TEXT | |
| `active_from`, `active_until` | TEXT | Reference only. Nothing prorates: these never alter an allocation |
| `is_active` | INTEGER 0/1 | |
| `email`, `phone`, `notes` | TEXT | |
| `created_at`, `updated_at` | TEXT | |

### `expense_definition`

A reusable category. **No category is hardcoded anywhere in the application** —
rent, electricity, groceries and the rest are rows here.

| Column | Type | Notes |
| --- | --- | --- |
| `expense_definition_id` | TEXT PK | |
| `apartment_id` | TEXT FK → apartment | |
| `name`, `category` | TEXT | `category` is the operator's own grouping |
| `period_rule` | TEXT | `previous_month` / `current_month` / `transaction_date` / `custom` |
| `default_allocation_method` | TEXT | `equal` / `weighted` / `custom` / `measured` / `host_guest` |
| `recurrence` | TEXT | `none` / `monthly` |
| `is_recurring`, `is_active` | INTEGER 0/1 | |
| `default_amount_paise` | INTEGER | A *suggestion* for generation; the actual amount may differ every month |
| `default_provider_name` | TEXT | |
| `default_participants_json` | TEXT | JSON array of resident IDs |
| `default_weights_json` | TEXT | JSON object, resident ID → whole-number weight |
| `day_of_month` | INTEGER 1–28 | Transaction date for a generated expense |
| `default_service_period`, `default_settlement_period` | TEXT | Reserved; unused by the current rules |
| `notes` | TEXT | |
| `sort_order` | INTEGER | Display order |
| `created_at`, `updated_at` | TEXT | |

---

## Expenses

### `expense_instance`

One household cost. The two period columns are the heart of the model.

| Column | Type | Notes |
| --- | --- | --- |
| `expense_id` | TEXT PK | |
| `apartment_id` | TEXT FK → apartment | Added in 0002; backfilled |
| `expense_definition_id` | TEXT FK | Nullable |
| `statement_id` | TEXT FK | Nullable until attached |
| `description` | TEXT | |
| `amount_paise` | INTEGER ≥ 0 | The household cost |
| `expense_date` | TEXT | The **transaction date** |
| `service_period` | TEXT `YYYY-MM` | The month the money was **for** |
| `settlement_period` | TEXT `YYYY-MM` | The statement month that **charges** it |
| `period_rule` | TEXT | The rule that produced those two |
| `due_date` | TEXT | Charge due date; drives oldest-first allocation. Defaults to the last day of the settlement month |
| `expense_period` | TEXT | **Legacy.** The prototype's single period. Kept so older data and callers still read correctly |
| `payer_resident_id` | TEXT FK | Who funded it — **ignored when a provider advance is linked** |
| `provider_name` | TEXT | |
| `allocation_method` | TEXT | How the shares were produced |
| `expense_kind` | TEXT | `general` / `electricity` |
| `reference`, `notes` | TEXT | |
| `generated_from_recurrence` | INTEGER 0/1 | Drives the duplicate-protection index |
| `is_settled` | INTEGER 0/1 | Legacy flag; settlement is derived from payment applications |
| `created_at`, `updated_at` | TEXT | |

### `expense_component`

A part of an expense with its own allocation rule. A general expense has one;
an electricity bill has one per itemised charge.

| Column | Type | Notes |
| --- | --- | --- |
| `component_id` | TEXT PK | |
| `expense_id` | TEXT FK → expense_instance | |
| `name` | TEXT | |
| `amount_paise` | INTEGER ≥ 0 | Components must sum to the expense exactly |
| `allocation_method` | TEXT | `measured` here is the engine's `usage` |
| `host_resident_id` | TEXT FK | For host-carries-guest components |

### `allocation`

One resident's share — a **charge**. These must sum to the expense exactly.
Zero shares are not written: they carry no financial meaning and are recorded
in the allocation basis instead.

| Column | Type | Notes |
| --- | --- | --- |
| `allocation_id` | TEXT PK | |
| `expense_id` | TEXT FK | |
| `component_id` | TEXT FK | |
| `resident_id` | TEXT FK | |
| `amount_paise` | INTEGER ≥ 0 | |
| `allocation_basis_json` | TEXT | Why this amount: method, weights, usage, payer |

`UNIQUE(expense_id, component_id, resident_id)`. The row's `rowid` is the
insertion sequence used as the oldest-first tie-break.

---

## Electricity

### `smart_plug_reading`

Operator-supplied allocation weights, stored as **whole watt-hours** so kWh
keeps exactly three decimal places. A guest's usage belongs inside the host's
reading.

| Column | Type | Notes |
| --- | --- | --- |
| `reading_id` | TEXT PK | |
| `resident_id`, `expense_id` | TEXT FK | |
| `device_label` | TEXT | |
| `period_start`, `period_end` | TEXT | |
| `energy_wh` | INTEGER ≥ 0 | kWh × 1000 |
| `source_note` | TEXT | |

### `electricity_bill`

The utility's own identifiers and window, plus the complete calculation as it
was committed.

| Column | Type | Notes |
| --- | --- | --- |
| `electricity_bill_id` | TEXT PK | |
| `expense_id` | TEXT FK, UNIQUE | One bill per expense |
| `bill_period_start`, `bill_period_end` | TEXT | The utility's consumption window |
| `settlement_period` | TEXT `YYYY-MM` | Distinct from the bill period |
| `bill_number`, `provider_name`, `notes` | TEXT | |
| `total_amount_paise` | INTEGER | The actual bill total — authoritative |
| `calculation_json` | TEXT | The engine's full output, kept for audit |
| `created_at` | TEXT | |

---

## Money movement

### `payment`

Four kinds, **none of which is a household expense**.

| Column | Type | Notes |
| --- | --- | --- |
| `payment_id` | TEXT PK | |
| `apartment_id` | TEXT FK | Added in 0002; backfilled |
| `payer_resident_id` | TEXT FK | |
| `amount_paise` | INTEGER > 0 | |
| `payment_date` | TEXT | |
| `payment_kind` | TEXT | `to_pool` / `provider_advance` / `refund` / `adjustment` (legacy) |
| `expense_id` | TEXT FK | **The funding link.** A linked advance replaces the expense's payer field |
| `payee_or_provider`, `reference`, `note` | TEXT | |
| `status` | TEXT | `active` / `reversed` / `reversal` |
| `reverses_payment_id` | TEXT FK → payment | Set on a reversal row |
| `created_at`, `created_by` | TEXT | |

A reversed payment and its mirrored reversal are **both excluded** from net
positions, so the money nets to nothing while both rows stay in the ledger.

### `payment_application`

Which charge a payment settled, and by how much.

| Column | Type | Notes |
| --- | --- | --- |
| `payment_application_id` | TEXT PK | |
| `payment_id` | TEXT FK | |
| `allocation_id` | TEXT FK | The charge |
| `amount_paise` | INTEGER > 0 | |
| `charge_due_date` | TEXT | The due date it was applied against |
| `created_at` | TEXT | |

`UNIQUE(payment_id, allocation_id)`. Triggers forbid exceeding either the
payment or the charge.

### `ledger_adjustment`

Signed corrections. **Append-only**, enforced by triggers: a mistake is
corrected by a reversing adjustment, never an edit.

| Column | Type | Notes |
| --- | --- | --- |
| `adjustment_id` | TEXT PK | |
| `statement_id` | TEXT FK | Nullable |
| `resident_id` | TEXT FK | |
| `amount_paise` | INTEGER, signed, non-zero | Positive credits the resident |
| `reason` | TEXT | Required |
| `effective_date` | TEXT | Drives the as-of cutoff |
| `created_at`, `created_by` | TEXT | |
| `reverses_adjustment_id` | TEXT FK → ledger_adjustment | Must be the exact negative |

---

## Statements

### `monthly_statement`

| Column | Type | Notes |
| --- | --- | --- |
| `statement_id` | TEXT PK | |
| `apartment_id` | TEXT FK | |
| `settlement_month` | TEXT `YYYY-MM` | `UNIQUE(apartment_id, settlement_month)` |
| `status` | TEXT | `draft` / `finalized_correction_window` / `closed` |
| `snapshot_json` | TEXT | The finalized snapshot, carrying its own SHA-256 |
| `created_at`, `finalized_at`, `correction_deadline`, `closed_at` | TEXT | |
| `finalized_by`, `closed_by` | TEXT | |
| `correction_window_hours` | INTEGER | 72 by default, per statement |
| `notes`, `updated_at` | TEXT | |

### `statement_revision`

Numbered, **append-only** corrections made inside the window.

| Column | Type | Notes |
| --- | --- | --- |
| `revision_id` | TEXT PK | |
| `statement_id` | TEXT FK | |
| `revision_number` | INTEGER | `UNIQUE(statement_id, revision_number)`; 1 is the finalization |
| `snapshot_json` | TEXT | |
| `reason` | TEXT | Required |
| `created_at`, `created_by` | TEXT | |

---

## Supporting tables

### `audit_log`

Append-only, enforced by triggers. One row per field changed.

| Column | Type |
| --- | --- |
| `audit_id` | TEXT PK |
| `apartment_id` | TEXT |
| `entity_type`, `entity_id`, `action` | TEXT |
| `field_name`, `old_value`, `new_value` | TEXT |
| `reason`, `actor`, `created_at` | TEXT |

### `app_setting`

Operator settings, such as the default apartment.

### `schema_migration`

| Column | Notes |
| --- | --- |
| `version` | INTEGER PK |
| `name`, `checksum`, `applied_at` | |
| `adopted` | 1 when the baseline was recognised in an existing prototype database rather than run |

---

## Integrity guards

### Indexes (20)

Primary keys and uniqueness aside, access-path indexes cover
`expense_instance` by settlement period, service period, apartment, statement
and definition; `allocation` by resident and expense; `payment` by apartment,
resident, expense and date; `payment_application` by allocation;
`ledger_adjustment` by resident; `smart_plug_reading` by expense;
`monthly_statement` by month; and `audit_log` by entity, apartment and time.

One is not an access path:

```sql
CREATE UNIQUE INDEX idx_recurrence_unique
    ON expense_instance(expense_definition_id, settlement_period)
 WHERE generated_from_recurrence = 1 AND expense_definition_id IS NOT NULL;
```

That partial unique index makes a duplicate generated recurring expense
impossible even under a race or a repeated request.

### Triggers (15)

| Guard | Raises |
| --- | --- |
| Closed statement snapshot/status immutable | `closed_statement` |
| Finalized statement cannot be deleted | `closed_statement` |
| Expenses in a closed statement cannot be changed | `closed_statement` |
| Expenses removable only from a draft | `locked_statement` |
| Allocations in a closed statement cannot change | `closed_statement` |
| Payment applications cannot exceed the payment (insert and update) | `overapplied_payment` |
| Payment applications cannot exceed the charge | `overapplied_charge` |
| `audit_log` no update, no delete | `append_only` |
| `statement_revision` no update, no delete | `append_only` |
| `ledger_adjustment` no update, no delete | `append_only` |

The API maps each marker to an HTTP 409 with the matching error code.

---

## Migrations

| Version | Name | What it does |
| --- | --- | --- |
| 0001 | `baseline` | The prototype's schema, unchanged. An existing prototype database is **adopted** at this version rather than re-run |
| 0002 | `periods_audit_and_integrity` | Service/settlement periods, due dates, expense kind and provenance; recurrence configuration; payment lifecycle and reversal links; adjustment effective dates; statement actors and window length; `electricity_bill`, `audit_log`, `app_setting`; the indexes and all fifteen triggers |
| 0003 | `resident_upi_id` | Adds optional UPI ID to resident profile for settlement deep-links |
| 0004 | `opening_balance` | Historical opening balances and opening debts carried forward into first statement |
| 0005 | `rent_master_lease` | Single master lease config (`rent_lease_config`), monthly rent cycle lifecycle (`rent_cycle`), landlord receipt blob storage (`rent_landlord_receipt`), and HRA slips (`rent_receipt`) |
| 0006 | `lease_lifecycle` | Contract term on `rent_lease_config` (start/end dates, duration, notice period, lock-in, security deposit, escalation clause, lease status and notice dates) and completed terms in `rent_lease_term` |

---

## Rent management tables

### `rent_lease_config`
Active lease parameters: landlord name, contact, PAN, bank/UPI parameters, monthly rent in paise, due day of month (1-28), and `requires_landlord_receipt` toggle.

Since migration 0006 it also holds the current contract term:

| Column | Type | Meaning |
| --- | --- | --- |
| `lease_start_date`, `lease_end_date` | TEXT | `YYYY-MM-DD`. The end date is inclusive; when not given it is the start date plus the duration, less a day. Both null until a term is entered |
| `duration_months` | INTEGER | Default 11 |
| `notice_period_days` | INTEGER | Default 30. The notice window opens this many days before the end date |
| `lock_in_months` | INTEGER | Default 6 |
| `security_deposit_paise` | INTEGER | Integer paise, nullable |
| `escalation_type` | TEXT | `percent` / `fixed` / `none` |
| `escalation_percent` | REAL | Default 5.0. A rate, not money; the projected rent is rounded to whole paise |
| `renewal_rent_paise` | INTEGER | The agreed next-term rent when `escalation_type` is `fixed` |
| `lease_status` | TEXT | `active` / `notice_served` / `vacated` / `renewed`. Only the first two are set by the application today |
| `notice_served_date`, `target_handover_date` | TEXT | Set when notice to vacate is recorded; cleared by a renewal |

### `rent_lease_term`
One row per completed term, written when the lease is renewed: `term_number`, `start_date`, `end_date`, `monthly_rent_paise`, and `escalation_applied_percent` (the change from that term's rent to the next term's). The current term is the one on `rent_lease_config`; its number is one more than the rows here.

### `rent_cycle`
State of a month's rent: links `settlement_month` to its `expense_instance`. Tracks `disbursement_status` (`pending` vs `disbursed`), funder (`disbursed_by` = `pool` vs `resident_advance`), and funding payment ID.

### `rent_landlord_receipt`
Binary blob storage of landlord receipt files (PDF/JPEG/PNG/WebP) with SHA-256 integrity hash, kept in SQLite so backups remain 100% self-contained.

### `rent_receipt`
Statutory Section 10(13A) IT Act compliant rent receipts issued to each participant upon landlord payout.

