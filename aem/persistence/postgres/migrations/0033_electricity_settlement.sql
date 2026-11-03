-- Electricity settled through the House Kitty: the grid (UPPCL) and the diesel
-- generator (DG), each a provider of its own.
--
-- A month's electricity is one expense with a component per provider. Each
-- flatmate is charged their own AC and an equal share of the rest, and pays it
-- into the kitty; the kitty pays UPPCL and DG separately, in tranches
-- (``service_disbursement``), as every household service is paid
-- (aem/services/electricity.py). A payout names its provider by its service,
-- so the flat has a live service for each: 'uppcl' and 'dg'.

ALTER TABLE household_service DROP CONSTRAINT household_service_service_type_check;
ALTER TABLE household_service ADD CONSTRAINT household_service_service_type_check
    CHECK (service_type IN ('furniture_rental', 'broadband', 'domestic_help', 'water_tanker',
                            'cooking_gas', 'electricity'));

-- Several live furniture subscriptions, and both electricity providers, at once.
DROP INDEX IF EXISTS ux_household_service_live_type;
CREATE UNIQUE INDEX ux_household_service_live_type
    ON household_service(apartment_id, service_type)
    WHERE status <> 'retired' AND service_type NOT IN ('furniture_rental', 'electricity');

CREATE TABLE electricity_service_config (
    service_id TEXT PRIMARY KEY REFERENCES household_service(service_id) ON DELETE CASCADE DEFERRABLE,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id) DEFERRABLE,
    provider_key TEXT NOT NULL CHECK (provider_key IN ('uppcl', 'dg')),
    provider_name TEXT NOT NULL CHECK (length(btrim(provider_name)) > 0),
    expense_definition_id TEXT REFERENCES expense_definition(expense_definition_id) DEFERRABLE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_electricity_service_config_apartment
    ON electricity_service_config(apartment_id);
