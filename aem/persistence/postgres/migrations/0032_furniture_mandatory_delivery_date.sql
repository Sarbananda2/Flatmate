-- A delivered furniture item carries the date it arrived in the flat.
--
-- An item is billed for every month from its delivery to its return
-- (aem/services/furniture_rental.py), so an item in the flat, or returned
-- from it, must say when it came; only one still awaiting delivery may not.
--
-- Items entered without one are dated the day they were entered -- or the
-- day they went back, for one entered after it was returned, so a return is
-- never before its delivery.

UPDATE furniture_rental_item
   SET delivery_date = LEAST(SUBSTRING(created_at FROM 1 FOR 10), return_date)
 WHERE delivery_date IS NULL AND status <> 'pending_delivery';

ALTER TABLE furniture_rental_item
    ADD CONSTRAINT furniture_rental_item_delivery_check
    CHECK (status = 'pending_delivery' OR delivery_date IS NOT NULL);
