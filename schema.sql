-- HISTORICAL BASELINE. This is the schema the original prototype created, kept
-- verbatim.
--
-- It is no longer applied by the application: the live schema is built by the
-- versioned migrations in aem/migrations/, and migration 0001 is a copy of
-- this file. A database created from this schema is *adopted* at version 1 and
-- upgraded in place, which is why the file is preserved unchanged --
-- tests/test_migrations.py builds a populated prototype database from it to
-- prove the upgrade loses nothing.
--
-- To see the current schema:  sqlite3 data/apartment.sqlite .schema
-- To change it:               add a new file in aem/migrations/
--
-- All money values are INTEGER paise.

PRAGMA foreign_keys = ON;

CREATE TABLE apartment (
    apartment_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    currency_code TEXT NOT NULL DEFAULT 'INR',
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    created_at TEXT NOT NULL
);

CREATE TABLE resident (
    resident_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id),
    display_name TEXT NOT NULL,
    active_from TEXT,
    active_until TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE expense_definition (
    expense_definition_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id),
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    period_rule TEXT NOT NULL CHECK (
        period_rule IN ('previous_month', 'current_month', 'transaction_date', 'custom')
    ),
    default_allocation_method TEXT NOT NULL CHECK (
        default_allocation_method IN ('equal', 'weighted', 'custom', 'measured', 'host_guest')
    ),
    is_recurring INTEGER NOT NULL DEFAULT 1 CHECK (is_recurring IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE monthly_statement (
    statement_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id),
    settlement_month TEXT NOT NULL, -- YYYY-MM
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'finalized_correction_window', 'closed')
    ),
    created_at TEXT NOT NULL,
    finalized_at TEXT,
    correction_deadline TEXT,
    closed_at TEXT,
    snapshot_json TEXT,
    UNIQUE(apartment_id, settlement_month)
);

CREATE TABLE expense_instance (
    expense_id TEXT PRIMARY KEY,
    expense_definition_id TEXT REFERENCES expense_definition(expense_definition_id),
    statement_id TEXT REFERENCES monthly_statement(statement_id),
    description TEXT NOT NULL,
    amount_paise INTEGER NOT NULL CHECK (amount_paise >= 0),
    expense_date TEXT NOT NULL,
    expense_period TEXT NOT NULL, -- YYYY-MM or date range identifier
    payer_resident_id TEXT REFERENCES resident(resident_id),
    provider_name TEXT,
    is_settled INTEGER NOT NULL DEFAULT 0 CHECK (is_settled IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE expense_component (
    component_id TEXT PRIMARY KEY,
    expense_id TEXT NOT NULL REFERENCES expense_instance(expense_id),
    name TEXT NOT NULL,
    amount_paise INTEGER NOT NULL CHECK (amount_paise >= 0),
    allocation_method TEXT NOT NULL CHECK (
        allocation_method IN ('equal', 'weighted', 'custom', 'measured', 'host_guest')
    ),
    host_resident_id TEXT REFERENCES resident(resident_id)
);

CREATE TABLE allocation (
    allocation_id TEXT PRIMARY KEY,
    expense_id TEXT NOT NULL REFERENCES expense_instance(expense_id),
    component_id TEXT REFERENCES expense_component(component_id),
    resident_id TEXT NOT NULL REFERENCES resident(resident_id),
    amount_paise INTEGER NOT NULL CHECK (amount_paise >= 0),
    allocation_basis_json TEXT,
    UNIQUE(expense_id, component_id, resident_id)
);

CREATE TABLE smart_plug_reading (
    reading_id TEXT PRIMARY KEY,
    resident_id TEXT NOT NULL REFERENCES resident(resident_id),
    expense_id TEXT NOT NULL REFERENCES expense_instance(expense_id),
    device_label TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    energy_wh INTEGER NOT NULL CHECK (energy_wh >= 0),
    source_note TEXT
);

CREATE TABLE payment (
    payment_id TEXT PRIMARY KEY,
    payer_resident_id TEXT NOT NULL REFERENCES resident(resident_id),
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    payment_date TEXT NOT NULL,
    payment_kind TEXT NOT NULL CHECK (
        payment_kind IN ('to_pool', 'provider_advance', 'refund', 'adjustment')
    ),
    expense_id TEXT REFERENCES expense_instance(expense_id), -- linked advance prevents double counting
    payee_or_provider TEXT,
    reference TEXT,
    note TEXT
);

CREATE TABLE payment_application (
    payment_application_id TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL REFERENCES payment(payment_id),
    allocation_id TEXT REFERENCES allocation(allocation_id),
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    charge_due_date TEXT,
    UNIQUE(payment_id, allocation_id)
);

CREATE TABLE ledger_adjustment (
    adjustment_id TEXT PRIMARY KEY,
    statement_id TEXT REFERENCES monthly_statement(statement_id),
    resident_id TEXT NOT NULL REFERENCES resident(resident_id),
    amount_paise INTEGER NOT NULL, -- positive/negative signed adjustment
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    reverses_adjustment_id TEXT REFERENCES ledger_adjustment(adjustment_id)
);

CREATE TABLE statement_revision (
    revision_id TEXT PRIMARY KEY,
    statement_id TEXT NOT NULL REFERENCES monthly_statement(statement_id),
    revision_number INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    UNIQUE(statement_id, revision_number)
);

CREATE INDEX idx_expense_instance_period ON expense_instance(expense_period);
CREATE INDEX idx_allocation_resident ON allocation(resident_id);
CREATE INDEX idx_payment_date ON payment(payment_date);
CREATE INDEX idx_statement_month ON monthly_statement(settlement_month);
