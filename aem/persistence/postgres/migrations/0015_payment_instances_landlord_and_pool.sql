-- The household pool as a payee, and who each deposit payment went to.
--
-- Alongside the residents there are two standing counterparties: the landlord,
-- whose payment details are on the lease, and the household pool, whose name,
-- bank account, IFSC and UPI ID are kept here on the apartment. Residents pay
-- the pool for their shares and settlements, so it needs a destination of its
-- own. The formats are the ones the owner contact's details already follow.
--
-- A deposit payment now says which kind of party received it: a resident (the
-- primary signatory, or a leaver bought out by their replacement), the
-- landlord, or the pool.

ALTER TABLE apartment
    ADD COLUMN pool_name TEXT NOT NULL DEFAULT 'Household Pool'
        CHECK (length(trim(pool_name)) > 0),
    ADD COLUMN pool_bank_account TEXT
        CHECK (pool_bank_account IS NULL OR pool_bank_account ~ '^[0-9]{9,18}$'),
    ADD COLUMN pool_bank_ifsc TEXT
        CHECK (pool_bank_ifsc IS NULL OR pool_bank_ifsc ~ '^[A-Z]{4}0[A-Z0-9]{6}$'),
    -- name@bank, the name 2 to 256 characters. PostgreSQL's regular expressions
    -- allow a repetition count of at most 255, so the name's length is checked
    -- on its own; together it is the same rule the application applies.
    ADD COLUMN pool_upi_id TEXT
        CHECK (pool_upi_id IS NULL
               OR (pool_upi_id ~ '^[A-Za-z0-9._-]+@[A-Za-z0-9.-]{2,64}$'
                   AND length(split_part(pool_upi_id, '@', 1)) BETWEEN 2 AND 256));

ALTER TABLE household_deposit_slot
    ADD COLUMN paid_to_party TEXT
        CHECK (paid_to_party IS NULL OR paid_to_party IN ('resident', 'landlord', 'pool'));

-- Slots already funded: money handed to a resident went to that resident;
-- any other funded slot was paid to the landlord, which until now was the
-- only other place a deposit could go.
UPDATE household_deposit_slot
   SET paid_to_party = CASE
       WHEN paid_to_resident_id IS NOT NULL THEN 'resident'
       WHEN funded_amount_paise > 0 THEN 'landlord'
       ELSE NULL
   END
 WHERE paid_to_party IS NULL;

-- household_deposit_transaction is append-only, so its existing rows are left
-- as they are: a row without a counterparty_party is read in the service from
-- what it does record.
ALTER TABLE household_deposit_transaction
    ADD COLUMN counterparty_party TEXT
        CHECK (counterparty_party IS NULL
               OR counterparty_party IN ('resident', 'landlord', 'pool'));
