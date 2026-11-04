-- House help and cooks: the household's workers, the roles each does, and each
-- month's attendance and salary.
--
-- A worker (a maid, a cook, a cleaner) may do several roles, each on its own
-- monthly salary, leave allowance and split: shared by every resident
-- ('equal') or only by those who opted in ('opt_in'). A month's attendance is
-- one record per role, with its deduction for leave beyond the allowance, any
-- bonus, and the salary it comes to.
--
-- The household's house help is a ``household_service`` of type
-- 'domestic_help'. Each month is billed as one expense, a component per role
-- and a charge per resident; residents pay their shares into the House Kitty,
-- and each worker is paid from it -- or by a resident from their own account --
-- in ``service_disbursement`` tranches that name the worker
-- (aem/services/househelp.py).

-- Workers / staff members (maid, cook, cleaner, etc.)
CREATE TABLE househelp_worker (
    worker_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (length(btrim(name)) > 0),
    phone TEXT,
    upi_id TEXT,
    notes TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_househelp_worker_apartment ON househelp_worker(apartment_id);

-- Distinct roles / services (a worker can have multiple roles)
CREATE TABLE househelp_role (
    role_id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL REFERENCES househelp_worker(worker_id) ON DELETE CASCADE,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id) ON DELETE CASCADE,
    role_name TEXT NOT NULL CHECK (length(btrim(role_name)) > 0),
    monthly_salary_paise BIGINT NOT NULL CHECK (monthly_salary_paise > 0),
    allowed_leaves_per_month INT NOT NULL DEFAULT 2 CHECK (allowed_leaves_per_month >= 0),
    split_method TEXT NOT NULL CHECK (split_method IN ('equal', 'opt_in')),
    created_at TEXT NOT NULL
);

CREATE INDEX idx_househelp_role_worker ON househelp_role(worker_id);
CREATE INDEX idx_househelp_role_apartment ON househelp_role(apartment_id);

-- Participating residents for opt-in roles
CREATE TABLE househelp_role_participant (
    role_id TEXT NOT NULL REFERENCES househelp_role(role_id) ON DELETE CASCADE,
    resident_id TEXT NOT NULL REFERENCES resident(resident_id) ON DELETE CASCADE,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, resident_id)
);

CREATE INDEX idx_househelp_role_participant_apartment
    ON househelp_role_participant(apartment_id);

-- Monthly attendance, leave decisions, extras & finalized role salary
CREATE TABLE househelp_monthly_record (
    record_id TEXT PRIMARY KEY,
    role_id TEXT NOT NULL REFERENCES househelp_role(role_id) ON DELETE CASCADE,
    settlement_month TEXT NOT NULL CHECK (settlement_month ~ '^[0-9]{4}-[0-1][0-9]$'),
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id) ON DELETE CASCADE,
    days_absent INT NOT NULL DEFAULT 0 CHECK (days_absent >= 0),
    excess_leave_action TEXT NOT NULL DEFAULT 'deduct' CHECK (excess_leave_action IN ('deduct', 'waive')),
    deduction_paise BIGINT NOT NULL DEFAULT 0 CHECK (deduction_paise >= 0),
    bonus_or_extra_paise BIGINT NOT NULL DEFAULT 0 CHECK (bonus_or_extra_paise >= 0),
    extra_notes TEXT,
    final_salary_paise BIGINT NOT NULL CHECK (final_salary_paise >= 0),
    UNIQUE (role_id, settlement_month)
);

CREATE INDEX idx_househelp_monthly_record_month
    ON househelp_monthly_record(apartment_id, settlement_month);

-- The house help service and the definition each month's bill is recorded
-- under, as the water supply keeps its own (0031): a bill under it is a
-- household service's, which the kitty pays out itself.
CREATE TABLE househelp_service_config (
    service_id TEXT PRIMARY KEY REFERENCES household_service(service_id) ON DELETE CASCADE DEFERRABLE,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id) DEFERRABLE,
    expense_definition_id TEXT REFERENCES expense_definition(expense_definition_id) DEFERRABLE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_househelp_service_config_apartment ON househelp_service_config(apartment_id);

-- Every worker is paid under the one house help service, so each payout names
-- the worker it paid. Other services' payouts name none.
ALTER TABLE service_disbursement
    ADD COLUMN worker_id TEXT REFERENCES househelp_worker(worker_id) DEFERRABLE;

CREATE INDEX idx_service_disbursement_worker
    ON service_disbursement(worker_id) WHERE worker_id IS NOT NULL;
