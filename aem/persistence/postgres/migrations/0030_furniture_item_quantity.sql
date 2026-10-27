-- Quantity for rented furniture items (e.g. 3 identical mattresses, 4 dining chairs).
--
-- ``monthly_rent_paise`` stays what the item bills, now for all of them
-- together; the rent for one is that over ``quantity``, which the service
-- keeps whole (aem/services/furniture_rental.py). Every item so far is one.

ALTER TABLE furniture_rental_item
    ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 1);
