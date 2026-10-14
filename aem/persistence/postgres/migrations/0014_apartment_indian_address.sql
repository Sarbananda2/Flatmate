-- The apartment's postal address, one part per column.
--
-- The name stays the apartment's label. Tenancy agreements, society gate
-- passes and HRA rent receipts need the address in its parts: flat, tower or
-- block, society, locality or sector, city, state and the 6-digit PIN code.
-- Every part is optional; a PIN code, when given, is six digits not starting
-- with 0.

ALTER TABLE apartment
    ADD COLUMN flat_number TEXT,
    ADD COLUMN tower_block TEXT,
    ADD COLUMN society_name TEXT,
    ADD COLUMN locality_street TEXT,
    ADD COLUMN city TEXT,
    ADD COLUMN state TEXT,
    ADD COLUMN pincode TEXT CHECK (pincode IS NULL OR pincode ~ '^[1-9][0-9]{5}$');

-- The household's own flat, whose address was until now only in its name.
UPDATE apartment
SET flat_number = '1805',
    tower_block = 'CG6',
    society_name = 'Supertech Capetown',
    locality_street = 'Sector 74',
    city = 'Noida',
    state = 'Uttar Pradesh',
    pincode = '201301'
WHERE apartment_id = 'flat-1805-cg6-supertech-capetown';
