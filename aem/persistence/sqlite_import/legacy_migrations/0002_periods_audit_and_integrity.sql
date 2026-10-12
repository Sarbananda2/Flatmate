-- Adds the accounting-period split, recurrence configuration, audit trail,
-- payment lifecycle, electricity bill metadata, and database-level integrity
-- guards. Existing rows are backfilled; nothing is dropped or rewritten.

-- 1. Residents: contact details and record timestamps.
ALTER TABLE resident ADD COLUMN email TEXT;
ALTER TABLE resident ADD COLUMN phone TEXT;
ALTER TABLE resident ADD COLUMN notes TEXT;
ALTER TABLE resident ADD COLUMN created_at TEXT;
ALTER TABLE resident ADD COLUMN updated_at TEXT;

-- 2. Expense definitions: recurrence and allocation defaults.
ALTER TABLE expense_definition ADD COLUMN recurrence TEXT NOT NULL DEFAULT 'none';
ALTER TABLE expense_definition ADD COLUMN default_amount_paise INTEGER;
ALTER TABLE expense_definition ADD COLUMN default_provider_name TEXT;
ALTER TABLE expense_definition ADD COLUMN default_participants_json TEXT;
ALTER TABLE expense_definition ADD COLUMN default_weights_json TEXT;
ALTER TABLE expense_definition ADD COLUMN day_of_month INTEGER;
ALTER TABLE expense_definition ADD COLUMN default_service_period TEXT;
ALTER TABLE expense_definition ADD COLUMN default_settlement_period TEXT;
ALTER TABLE expense_definition ADD COLUMN notes TEXT;
ALTER TABLE expense_definition ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 100;
ALTER TABLE expense_definition ADD COLUMN created_at TEXT;
ALTER TABLE expense_definition ADD COLUMN updated_at TEXT;

UPDATE expense_definition
   SET recurrence = CASE WHEN is_recurring = 1 THEN 'monthly' ELSE 'none' END
 WHERE recurrence = 'none';

-- 3. Expenses: service vs settlement period, due date, provenance, notes.
ALTER TABLE expense_instance ADD COLUMN service_period TEXT;
ALTER TABLE expense_instance ADD COLUMN settlement_period TEXT;
ALTER TABLE expense_instance ADD COLUMN period_rule TEXT;
ALTER TABLE expense_instance ADD COLUMN due_date TEXT;
ALTER TABLE expense_instance ADD COLUMN allocation_method TEXT;
ALTER TABLE expense_instance ADD COLUMN expense_kind TEXT NOT NULL DEFAULT 'general';
ALTER TABLE expense_instance ADD COLUMN apartment_id TEXT REFERENCES apartment(apartment_id);
ALTER TABLE expense_instance ADD COLUMN reference TEXT;
ALTER TABLE expense_instance ADD COLUMN notes TEXT;
ALTER TABLE expense_instance ADD COLUMN generated_from_recurrence INTEGER NOT NULL DEFAULT 0
    CHECK (generated_from_recurrence IN (0, 1));
ALTER TABLE expense_instance ADD COLUMN updated_at TEXT;

-- Backfill: the prototype used expense_period as the settlement month. Where it
-- is not a YYYY-MM value, fall back to the month of the transaction date.
UPDATE expense_instance
   SET settlement_period = CASE
         WHEN expense_period GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]' THEN expense_period
         ELSE substr(expense_date, 1, 7) END
 WHERE settlement_period IS NULL;

UPDATE expense_instance SET service_period = settlement_period WHERE service_period IS NULL;

UPDATE expense_instance
   SET due_date = date(settlement_period || '-01', '+1 month', '-1 day')
 WHERE due_date IS NULL;

UPDATE expense_instance SET period_rule = 'custom' WHERE period_rule IS NULL;

UPDATE expense_instance
   SET expense_kind = 'electricity'
 WHERE expense_kind = 'general'
   AND EXISTS (SELECT 1 FROM smart_plug_reading s WHERE s.expense_id = expense_instance.expense_id);

-- Derive the owning apartment from the statement, else from any allocated resident.
UPDATE expense_instance
   SET apartment_id = COALESCE(
         (SELECT s.apartment_id FROM monthly_statement s
           WHERE s.statement_id = expense_instance.statement_id),
         (SELECT r.apartment_id FROM allocation a
            JOIN resident r ON r.resident_id = a.resident_id
           WHERE a.expense_id = expense_instance.expense_id LIMIT 1),
         (SELECT r.apartment_id FROM resident r
           WHERE r.resident_id = expense_instance.payer_resident_id))
 WHERE apartment_id IS NULL;

-- 4. Payments: lifecycle, reversals, timestamps.
ALTER TABLE payment ADD COLUMN apartment_id TEXT REFERENCES apartment(apartment_id);
ALTER TABLE payment ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'reversed', 'reversal'));
ALTER TABLE payment ADD COLUMN reverses_payment_id TEXT REFERENCES payment(payment_id);
ALTER TABLE payment ADD COLUMN created_at TEXT;
ALTER TABLE payment ADD COLUMN created_by TEXT;

UPDATE payment
   SET apartment_id = (SELECT r.apartment_id FROM resident r
                        WHERE r.resident_id = payment.payer_resident_id)
 WHERE apartment_id IS NULL;

-- 5. Payment applications: record when the application was made.
ALTER TABLE payment_application ADD COLUMN created_at TEXT;

-- 6. Adjustments: an effective date distinct from the record's creation time.
ALTER TABLE ledger_adjustment ADD COLUMN effective_date TEXT;
UPDATE ledger_adjustment SET effective_date = substr(created_at, 1, 10)
 WHERE effective_date IS NULL;

-- 7. Statements: who acted, and the window length actually used.
ALTER TABLE monthly_statement ADD COLUMN finalized_by TEXT;
ALTER TABLE monthly_statement ADD COLUMN closed_by TEXT;
ALTER TABLE monthly_statement ADD COLUMN correction_window_hours INTEGER NOT NULL DEFAULT 72;
ALTER TABLE monthly_statement ADD COLUMN notes TEXT;
ALTER TABLE monthly_statement ADD COLUMN updated_at TEXT;

-- 8. Electricity bill metadata: bill period and settlement period are distinct,
--    and the actual utility bill's own identifiers are worth keeping.
CREATE TABLE electricity_bill (
    electricity_bill_id TEXT PRIMARY KEY,
    expense_id TEXT NOT NULL UNIQUE REFERENCES expense_instance(expense_id),
    bill_period_start TEXT NOT NULL,
    bill_period_end TEXT NOT NULL,
    settlement_period TEXT NOT NULL,
    bill_number TEXT,
    provider_name TEXT,
    total_amount_paise INTEGER NOT NULL CHECK (total_amount_paise >= 0),
    calculation_json TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL
);

-- 9. Append-only audit trail.
CREATE TABLE audit_log (
    audit_id TEXT PRIMARY KEY,
    apartment_id TEXT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_audit_entity ON audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_created ON audit_log(created_at);
CREATE INDEX idx_audit_apartment ON audit_log(apartment_id);

-- 10. Operator settings (single-operator app; no permission system).
CREATE TABLE app_setting (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 11. Indexes supporting the new access paths.
CREATE INDEX idx_expense_settlement_period ON expense_instance(settlement_period);
CREATE INDEX idx_expense_service_period ON expense_instance(service_period);
CREATE INDEX idx_expense_apartment ON expense_instance(apartment_id);
CREATE INDEX idx_expense_statement ON expense_instance(statement_id);
CREATE INDEX idx_expense_definition_link ON expense_instance(expense_definition_id);
CREATE INDEX idx_allocation_expense ON allocation(expense_id);
CREATE INDEX idx_payment_apartment ON payment(apartment_id);
CREATE INDEX idx_payment_resident ON payment(payer_resident_id);
CREATE INDEX idx_payment_expense ON payment(expense_id);
CREATE INDEX idx_payment_application_allocation ON payment_application(allocation_id);
CREATE INDEX idx_adjustment_resident ON ledger_adjustment(resident_id);
CREATE INDEX idx_reading_expense ON smart_plug_reading(expense_id);

-- Duplicate protection for recurring generation: one generated expense per
-- definition per settlement period.
CREATE UNIQUE INDEX idx_recurrence_unique
    ON expense_instance(expense_definition_id, settlement_period)
 WHERE generated_from_recurrence = 1 AND expense_definition_id IS NOT NULL;

-- 12. Database-level integrity guards. These back up the service layer; they
--     are the reason a closed statement cannot be modified even by a direct
--     SQL mistake.

CREATE TRIGGER trg_statement_closed_is_immutable
BEFORE UPDATE ON monthly_statement FOR EACH ROW
WHEN OLD.status = 'closed'
 AND (NEW.snapshot_json IS NOT OLD.snapshot_json
      OR NEW.status <> OLD.status
      OR NEW.settlement_month <> OLD.settlement_month
      OR NEW.apartment_id <> OLD.apartment_id
      OR NEW.finalized_at IS NOT OLD.finalized_at)
BEGIN
    SELECT RAISE(ABORT, 'closed_statement: a closed statement snapshot is immutable');
END;

CREATE TRIGGER trg_statement_closed_no_delete
BEFORE DELETE ON monthly_statement FOR EACH ROW
WHEN OLD.status <> 'draft'
BEGIN
    SELECT RAISE(ABORT, 'closed_statement: finalized statements cannot be deleted');
END;

CREATE TRIGGER trg_expense_locked_no_update
BEFORE UPDATE ON expense_instance FOR EACH ROW
WHEN OLD.statement_id IS NOT NULL
 AND (SELECT status FROM monthly_statement WHERE statement_id = OLD.statement_id) = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed_statement: expenses in a closed statement cannot be changed');
END;

CREATE TRIGGER trg_expense_locked_no_delete
BEFORE DELETE ON expense_instance FOR EACH ROW
WHEN OLD.statement_id IS NOT NULL
 AND (SELECT status FROM monthly_statement WHERE statement_id = OLD.statement_id) <> 'draft'
BEGIN
    SELECT RAISE(ABORT, 'locked_statement: expenses can only be removed from a draft statement');
END;

CREATE TRIGGER trg_allocation_locked_no_insert
BEFORE INSERT ON allocation FOR EACH ROW
WHEN (SELECT status FROM monthly_statement
       WHERE statement_id = (SELECT statement_id FROM expense_instance
                              WHERE expense_id = NEW.expense_id)) = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed_statement: allocations in a closed statement cannot be changed');
END;

CREATE TRIGGER trg_allocation_locked_no_delete
BEFORE DELETE ON allocation FOR EACH ROW
WHEN (SELECT status FROM monthly_statement
       WHERE statement_id = (SELECT statement_id FROM expense_instance
                              WHERE expense_id = OLD.expense_id)) = 'closed'
BEGIN
    SELECT RAISE(ABORT, 'closed_statement: allocations in a closed statement cannot be changed');
END;

-- A payment can never be allocated beyond its own amount.
CREATE TRIGGER trg_payment_application_insert_within_payment
AFTER INSERT ON payment_application FOR EACH ROW
WHEN (SELECT COALESCE(SUM(amount_paise), 0) FROM payment_application
       WHERE payment_id = NEW.payment_id)
   > (SELECT amount_paise FROM payment WHERE payment_id = NEW.payment_id)
BEGIN
    SELECT RAISE(ABORT, 'overapplied_payment: payment applications exceed the payment amount');
END;

CREATE TRIGGER trg_payment_application_update_within_payment
AFTER UPDATE ON payment_application FOR EACH ROW
WHEN (SELECT COALESCE(SUM(amount_paise), 0) FROM payment_application
       WHERE payment_id = NEW.payment_id)
   > (SELECT amount_paise FROM payment WHERE payment_id = NEW.payment_id)
BEGIN
    SELECT RAISE(ABORT, 'overapplied_payment: payment applications exceed the payment amount');
END;

-- A charge can never be settled beyond its own amount.
CREATE TRIGGER trg_payment_application_within_allocation
AFTER INSERT ON payment_application FOR EACH ROW
WHEN NEW.allocation_id IS NOT NULL
 AND (SELECT COALESCE(SUM(amount_paise), 0) FROM payment_application
       WHERE allocation_id = NEW.allocation_id)
   > (SELECT amount_paise FROM allocation WHERE allocation_id = NEW.allocation_id)
BEGIN
    SELECT RAISE(ABORT, 'overapplied_charge: payment applications exceed the charge amount');
END;

-- Audit trail, statement revisions, and adjustments are append-only.
CREATE TRIGGER trg_audit_log_no_update
BEFORE UPDATE ON audit_log FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'append_only: audit_log cannot be modified'); END;

CREATE TRIGGER trg_audit_log_no_delete
BEFORE DELETE ON audit_log FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'append_only: audit_log cannot be deleted'); END;

CREATE TRIGGER trg_statement_revision_no_update
BEFORE UPDATE ON statement_revision FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'append_only: statement revisions cannot be modified'); END;

CREATE TRIGGER trg_statement_revision_no_delete
BEFORE DELETE ON statement_revision FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'append_only: statement revisions cannot be deleted'); END;

CREATE TRIGGER trg_ledger_adjustment_no_update
BEFORE UPDATE ON ledger_adjustment FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'append_only: adjustments are corrected by a reversing adjustment'); END;

CREATE TRIGGER trg_ledger_adjustment_no_delete
BEFORE DELETE ON ledger_adjustment FOR EACH ROW
BEGIN SELECT RAISE(ABORT, 'append_only: adjustments are corrected by a reversing adjustment'); END;
