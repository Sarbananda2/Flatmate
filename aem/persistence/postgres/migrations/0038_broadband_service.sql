-- Broadband & WiFi: the flat's internet connection on a fixed monthly plan.
--
-- A household's broadband is a ``household_service`` of type 'broadband' with
-- its plan here: the ISP, the account, the fixed monthly rate, the speed, the
-- WiFi network's name, and the definition each month's bill is recorded under.
-- The bill is an ordinary expense split equally between the residents taking
-- part, and the ISP is paid from the kitty in ``service_disbursement``
-- tranches, as every household service is (aem/services/broadband.py).
--
-- The WiFi password is not kept here.

CREATE TABLE broadband_service_config (
    service_id TEXT PRIMARY KEY REFERENCES household_service(service_id) ON DELETE CASCADE DEFERRABLE,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id) DEFERRABLE,
    provider_name TEXT NOT NULL DEFAULT 'Airtel Xstream Fiber',
    account_number TEXT,
    monthly_rate_paise BIGINT NOT NULL DEFAULT 0 CHECK (monthly_rate_paise >= 0),
    speed_mbps INTEGER CHECK (speed_mbps IS NULL OR speed_mbps > 0),
    billing_day_of_month INTEGER CHECK (billing_day_of_month IS NULL OR (billing_day_of_month >= 1 AND billing_day_of_month <= 31)),
    expense_definition_id TEXT REFERENCES expense_definition(expense_definition_id) DEFERRABLE,
    wifi_ssid TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_broadband_service_config_apartment ON broadband_service_config(apartment_id);
