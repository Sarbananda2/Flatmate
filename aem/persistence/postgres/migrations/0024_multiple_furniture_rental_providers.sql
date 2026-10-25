-- More than one furniture rental provider at a time.
--
-- A household may rent from Furlenco, Rentomojo and Cityfurnish at once, each
-- its own subscription with its own deposit and items. Every other type of
-- household service still has at most one live service per flat; a retired
-- one stays for the record, as before.

DROP INDEX IF EXISTS ux_household_service_live_type;
CREATE UNIQUE INDEX ux_household_service_live_type
    ON household_service(apartment_id, service_type)
    WHERE status <> 'retired' AND service_type <> 'furniture_rental';
