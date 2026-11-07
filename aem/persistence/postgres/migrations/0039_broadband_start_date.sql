-- The day the flat's broadband connection began. A month before it has no
-- broadband bill: none is recorded for it, and the screen says the connection
-- had not started yet. The month it began in is billed in full, as every month
-- is, since nothing prorates (aem/services/broadband.py).
--
-- A plan already on file is taken to have begun on the day it was set up, or
-- on the first day of the earliest month billed under its definition if that
-- is earlier, so no month already billed falls before it.

ALTER TABLE broadband_service_config ADD COLUMN start_date TEXT;

UPDATE broadband_service_config c
   SET start_date = LEAST(
       substr(c.created_at, 1, 10),
       COALESCE((SELECT min(e.settlement_period) || '-01'
                   FROM expense_instance e
                  WHERE e.apartment_id = c.apartment_id
                    AND e.expense_definition_id = c.expense_definition_id
                    AND e.settlement_period IS NOT NULL),
                substr(c.created_at, 1, 10)));

ALTER TABLE broadband_service_config
    ALTER COLUMN start_date SET NOT NULL,
    ADD CONSTRAINT broadband_service_config_start_date_check
        CHECK (start_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$');
