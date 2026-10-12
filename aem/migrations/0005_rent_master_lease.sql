-- Rent under a single master lease, split equally.
--
-- The household has one lease with one landlord for one monthly rent. Each
-- settlement month gets a rent cycle that wraps an ordinary expense_instance:
-- the expense and its allocation rows remain the accounting record, and the
-- cycle adds what an expense cannot say -- whether the landlord has been paid,
-- how, and with which bank reference.
--
-- All money columns are INTEGER paise. typeof() is checked as well as the sign
-- because a REAL such as 1500.5 would otherwise satisfy "> 0".

-- 1. The lease. One active row per apartment.
CREATE TABLE rent_lease_config (
    config_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id),
    -- The expense definition rent is recorded under (category 'Housing'). It is
    -- what stops the recurring generator and the rent cycle from both creating
    -- a rent expense for the same month.
    expense_definition_id TEXT REFERENCES expense_definition(expense_definition_id),
    landlord_name TEXT NOT NULL CHECK (length(trim(landlord_name)) > 0),
    landlord_phone TEXT,
    landlord_email TEXT,
    landlord_pan TEXT,
    landlord_bank_account TEXT,
    landlord_bank_ifsc TEXT,
    landlord_upi_id TEXT,
    monthly_rent_paise INTEGER NOT NULL
        CHECK (typeof(monthly_rent_paise) = 'integer' AND monthly_rent_paise > 0),
    due_day_of_month INTEGER NOT NULL DEFAULT 5 CHECK (due_day_of_month BETWEEN 1 AND 28),
    -- Most landlords confirm by bank reference only, so this is off by default.
    requires_landlord_receipt INTEGER NOT NULL DEFAULT 0
        CHECK (requires_landlord_receipt IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE UNIQUE INDEX idx_rent_lease_config_active
    ON rent_lease_config(apartment_id) WHERE is_active = 1;

-- 2. One rent cycle per apartment per settlement month.
--
-- total_rent_paise and participant_count record the cycle as generated and are
-- brought up to date when the landlord is paid, after which the row is frozen.
-- Who funded the payout is unknown until then, so disbursed_by stays NULL while
-- the cycle is pending rather than naming a funding source that has not been used.
CREATE TABLE rent_cycle (
    cycle_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id),
    settlement_month TEXT NOT NULL
        CHECK (settlement_month GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]'),
    expense_id TEXT REFERENCES expense_instance(expense_id),
    total_rent_paise INTEGER NOT NULL
        CHECK (typeof(total_rent_paise) = 'integer' AND total_rent_paise > 0),
    participant_count INTEGER NOT NULL CHECK (participant_count > 0),
    disbursement_status TEXT NOT NULL CHECK (disbursement_status IN ('pending', 'disbursed')),
    disbursed_at TEXT,              -- the date the landlord was paid, YYYY-MM-DD
    disbursement_reference TEXT,    -- UTR / UPI transaction ID
    disbursed_by TEXT CHECK (disbursed_by IN ('pool', 'resident_advance')),
    funding_resident_id TEXT REFERENCES resident(resident_id),
    funding_payment_id TEXT REFERENCES payment(payment_id),
    created_at TEXT NOT NULL,
    UNIQUE(apartment_id, settlement_month),
    CHECK (
        (disbursement_status = 'pending'
            AND disbursed_at IS NULL AND disbursed_by IS NULL
            AND disbursement_reference IS NULL
            AND funding_resident_id IS NULL AND funding_payment_id IS NULL)
        OR
        (disbursement_status = 'disbursed'
            AND disbursed_at IS NOT NULL AND disbursement_reference IS NOT NULL
            AND ((disbursed_by = 'pool'
                    AND funding_resident_id IS NULL AND funding_payment_id IS NULL)
              OR (disbursed_by = 'resident_advance'
                    AND funding_resident_id IS NOT NULL AND funding_payment_id IS NOT NULL)))
    )
);

CREATE INDEX idx_rent_cycle_expense ON rent_cycle(expense_id);

CREATE TRIGGER trg_rent_cycle_disbursed_no_update
BEFORE UPDATE ON rent_cycle FOR EACH ROW
WHEN OLD.disbursement_status = 'disbursed'
BEGIN
    SELECT RAISE(ABORT, 'append_only: a disbursed rent cycle cannot be changed');
END;

CREATE TRIGGER trg_rent_cycle_disbursed_no_delete
BEFORE DELETE ON rent_cycle FOR EACH ROW
WHEN OLD.disbursement_status = 'disbursed'
BEGIN
    SELECT RAISE(ABORT, 'append_only: a disbursed rent cycle cannot be deleted');
END;

-- 3. HRA rent receipts, one per participant, written when the landlord is paid.
--
-- Each row carries everything the printed slip states, plus a SHA-256 over
-- those same fields, so a slip can be checked against the database later.
-- landlord_pan is '' when the lease has no PAN on file.
CREATE TABLE rent_receipt (
    receipt_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES rent_cycle(cycle_id),
    resident_id TEXT NOT NULL REFERENCES resident(resident_id),
    settlement_month TEXT NOT NULL,
    amount_paise INTEGER NOT NULL
        CHECK (typeof(amount_paise) = 'integer' AND amount_paise > 0),
    landlord_name TEXT NOT NULL,
    landlord_pan TEXT NOT NULL,
    verification_hash TEXT NOT NULL CHECK (length(verification_hash) = 64),
    created_at TEXT NOT NULL,
    UNIQUE(cycle_id, resident_id)
);

CREATE TRIGGER trg_rent_receipt_no_update
BEFORE UPDATE ON rent_receipt FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'append_only: rent receipts cannot be modified'); END;

CREATE TRIGGER trg_rent_receipt_no_delete
BEFORE DELETE ON rent_receipt FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'append_only: rent receipts cannot be deleted'); END;

-- 4. How a payment was made: UPI, bank transfer, cash. Asked for when a resident
--    pays their rent share; NULL for payments recorded without it.
ALTER TABLE payment ADD COLUMN payment_mode TEXT
    CHECK (payment_mode IS NULL OR payment_mode IN ('upi', 'bank_transfer', 'cash', 'other'));

-- 5. The landlord's own receipt, for leases where one is issued. Kept inside
--    the database so that copying the database file is still a complete backup.
--
--    It usually arrives after the rent is paid, so it is attached to a cycle
--    whenever it turns up and can be replaced if the wrong file was chosen.
--    Each attachment and replacement is recorded in the audit log with the
--    file's SHA-256, which is why this table is not append-only itself.
CREATE TABLE rent_landlord_receipt (
    cycle_id TEXT PRIMARY KEY REFERENCES rent_cycle(cycle_id),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    content BLOB NOT NULL,
    uploaded_at TEXT NOT NULL
);
