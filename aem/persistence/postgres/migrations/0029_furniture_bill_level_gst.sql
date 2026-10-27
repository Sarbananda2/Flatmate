-- GST is charged on a month's furniture bill, not on each item.
--
-- A provider's invoice adds GST to the items' rents together, so an item now
-- keeps only its rent before GST (``monthly_rent_paise``), and the month's bill
-- adds GST at the subscription's rate on the subtotal of the items billed,
-- rounded half up to the paisa (aem/services/furniture_rental.py). A month's
-- bill may be set to the invoice's own total, where that rounds its GST
-- another way; the rate and that total are kept with the month's charges.
--
-- Migration 0027 gave each item a rent before GST, a flag and a rate of its
-- own. Each item keeps that rent before GST, and the rest goes. Bills already
-- recorded keep what they were billed: a month gone by never changes, and an
-- open month is billed afresh from its items.
--
-- ``gst_rate_percent`` on the subscription is the rate a new month's bill
-- starts at: 18% unless set, to two decimal places.

UPDATE furniture_rental_item SET monthly_rent_paise = base_rent_paise;

ALTER TABLE furniture_rental_item DROP CONSTRAINT furniture_rental_item_gst_check;

ALTER TABLE furniture_rental_item
    DROP COLUMN base_rent_paise,
    DROP COLUMN is_gst_exclusive,
    DROP COLUMN gst_rate_percent;

ALTER TABLE furniture_rental_config
    ADD COLUMN gst_rate_percent DOUBLE PRECISION NOT NULL DEFAULT 18.0
        CHECK (gst_rate_percent >= 0 AND gst_rate_percent <= 100);
