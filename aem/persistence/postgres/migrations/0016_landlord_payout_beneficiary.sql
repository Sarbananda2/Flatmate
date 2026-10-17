-- Whose name the landlord's payout account is in.
--
-- The landlord on the lease is the property's legal owner: the name on the
-- agreement and on HRA receipts. Rent and deposits are sometimes paid into an
-- account in someone else's name -- a spouse, a parent, a family business. That
-- name is kept beside the owner's, never in place of it, on the lease and on
-- the owner contact alike; null means the account is the owner's own.

ALTER TABLE rent_lease_config
    ADD COLUMN payout_beneficiary_name TEXT
        CHECK (payout_beneficiary_name IS NULL OR length(trim(payout_beneficiary_name)) > 0);

ALTER TABLE household_contact
    ADD COLUMN beneficiary_name TEXT
        CHECK (beneficiary_name IS NULL OR length(trim(beneficiary_name)) > 0);
