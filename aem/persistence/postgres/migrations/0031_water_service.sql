-- Fixed pipeline water contract (e.g. society connection, fixed monthly charge).
--
-- A household's water supply is a ``household_service`` of type 'water_tanker'
-- with its contract here: who supplies it, the fixed monthly rate, and the
-- definition each month's bill is recorded under. The bill is an ordinary
-- expense split equally between the residents taking part, and the supplier is
-- paid from the kitty in ``service_disbursement`` tranches, as every household
-- service is (aem/services/water.py).

CREATE TABLE water_service_config (
    service_id TEXT PRIMARY KEY REFERENCES household_service(service_id) ON DELETE CASCADE DEFERRABLE,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id) DEFERRABLE,
    provider_name TEXT NOT NULL DEFAULT 'Society Water Supply',
    monthly_rate_paise BIGINT NOT NULL DEFAULT 0 CHECK (monthly_rate_paise >= 0),
    consumer_id TEXT,
    billing_day_of_month INTEGER CHECK (billing_day_of_month IS NULL OR (billing_day_of_month >= 1 AND billing_day_of_month <= 31)),
    expense_definition_id TEXT REFERENCES expense_definition(expense_definition_id) DEFERRABLE,
    pipeline_notes TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_water_service_config_apartment ON water_service_config(apartment_id);
