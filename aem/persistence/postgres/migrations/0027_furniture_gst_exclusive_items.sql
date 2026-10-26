-- A rented item priced before GST.
--
-- Providers quote some items exclusive of GST. Such an item keeps the rent it
-- was quoted at (``base_rent_paise``) and is billed that plus GST at its own
-- rate (``gst_rate_percent``, 18% unless set, to two decimal places), rounded
-- half up to the paisa (aem/services/furniture_rental.py); ``monthly_rent_paise``
-- stays what it is billed, GST included, so every bill, share and payout is
-- reckoned on it as before. An item priced with GST included, as every item so
-- far, has the two the same.
--
-- ``is_gst_exclusive`` is a 0/1 flag, as the schema's other flags are.

ALTER TABLE furniture_rental_item
    ADD COLUMN base_rent_paise BIGINT,
    ADD COLUMN is_gst_exclusive INTEGER NOT NULL DEFAULT 0
        CHECK (is_gst_exclusive IN (0, 1)),
    ADD COLUMN gst_rate_percent DOUBLE PRECISION NOT NULL DEFAULT 18.0
        CHECK (gst_rate_percent >= 0 AND gst_rate_percent <= 100);

UPDATE furniture_rental_item SET base_rent_paise = monthly_rent_paise;

ALTER TABLE furniture_rental_item ALTER COLUMN base_rent_paise SET NOT NULL;

-- GST only ever adds to an item's rent, and only to one priced before it.
ALTER TABLE furniture_rental_item ADD CONSTRAINT furniture_rental_item_gst_check CHECK (
    base_rent_paise > 0
    AND (CASE WHEN is_gst_exclusive = 1 THEN monthly_rent_paise >= base_rent_paise
              ELSE monthly_rent_paise = base_rent_paise END));
