-- Opening balances: what each resident owed, or was owed, before the household
-- started recording in this application.
--
-- One row per resident at most. The amount is signed with the ledger's own
-- convention: positive means the household owes the resident (a credit they
-- bring in); negative means the resident owes the household (a debt they bring
-- in). Zero is meaningless and is rejected: clear the row instead.
--
-- A debt behaves like the resident's oldest charge, so pool payments clear it
-- first. Those applications are recorded in payment_application against the
-- new opening_balance_id column, with allocation_id left NULL.
CREATE TABLE opening_balance (
    opening_balance_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id),
    resident_id TEXT NOT NULL UNIQUE REFERENCES resident(resident_id),
    amount_paise INTEGER NOT NULL CHECK (amount_paise <> 0),
    as_of_date TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_opening_balance_apartment ON opening_balance(apartment_id);

ALTER TABLE payment_application
    ADD COLUMN opening_balance_id TEXT REFERENCES opening_balance(opening_balance_id);

-- One application row per payment per opening balance, mirroring the existing
-- UNIQUE(payment_id, allocation_id) for ordinary charges.
CREATE UNIQUE INDEX idx_payment_application_opening
    ON payment_application(payment_id, opening_balance_id)
 WHERE opening_balance_id IS NOT NULL;

-- An application settles exactly one thing: a charge or an opening debt.
CREATE TRIGGER trg_payment_application_single_target
BEFORE INSERT ON payment_application FOR EACH ROW
WHEN NEW.allocation_id IS NOT NULL AND NEW.opening_balance_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'invalid_application: an application targets a charge or an opening balance, not both');
END;

-- An opening debt can never be settled beyond its own amount, and an opening
-- credit (a positive amount) cannot be "settled" at all.
CREATE TRIGGER trg_payment_application_within_opening_balance
AFTER INSERT ON payment_application FOR EACH ROW
WHEN NEW.opening_balance_id IS NOT NULL
 AND (SELECT COALESCE(SUM(amount_paise), 0) FROM payment_application
       WHERE opening_balance_id = NEW.opening_balance_id)
   > -(SELECT amount_paise FROM opening_balance
        WHERE opening_balance_id = NEW.opening_balance_id)
BEGIN
    SELECT RAISE(ABORT, 'overapplied_charge: payment applications exceed the opening balance owed');
END;

CREATE TRIGGER trg_payment_application_update_within_opening_balance
AFTER UPDATE ON payment_application FOR EACH ROW
WHEN NEW.opening_balance_id IS NOT NULL
 AND (SELECT COALESCE(SUM(amount_paise), 0) FROM payment_application
       WHERE opening_balance_id = NEW.opening_balance_id)
   > -(SELECT amount_paise FROM opening_balance
        WHERE opening_balance_id = NEW.opening_balance_id)
BEGIN
    SELECT RAISE(ABORT, 'overapplied_charge: payment applications exceed the opening balance owed');
END;

