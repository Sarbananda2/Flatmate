-- An open month's bill may go below what the landlord or a provider was paid.
--
-- While a month's statement is a draft, its rent or a service's bill follows
-- the lease or the items down as well as up, after the landlord or provider has
-- been paid. The service layer (aem/services/bill_shares.py) keeps each
-- resident's own payout whole, split again between their new share and the
-- extra the House Kitty owes them, and brings the kitty's own payouts down to
-- the new bill, newest first, so what it paid beyond is there to pay the
-- residents back.
--
-- So a payout changed in place is no longer held to the bill, and nor is one
-- added while a bill's payouts are split again: the transaction names that bill
-- in ``aem.rebalancing_bill`` for as long as it takes. A payout recorded at any
-- other time still never takes a bill's payouts beyond it.

DROP TRIGGER trg_service_disbursement_update_within_bill ON service_disbursement;
DROP TRIGGER trg_rent_disbursement_update_within_rent ON rent_disbursement;

CREATE OR REPLACE FUNCTION aem_service_disbursement_within_bill() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('aem.rebalancing_bill', true) = NEW.expense_id THEN
        RETURN NULL;
    END IF;
    IF (SELECT COALESCE(SUM(amount_paise), 0) FROM service_disbursement
         WHERE expense_id = NEW.expense_id)
       > (SELECT amount_paise FROM expense_instance WHERE expense_id = NEW.expense_id) THEN
        RAISE EXCEPTION 'overpaid_service: the payouts to the provider would exceed the bill';
    END IF;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION aem_rent_disbursement_within_rent() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('aem.rebalancing_bill', true)
       = (SELECT expense_id FROM rent_cycle WHERE cycle_id = NEW.cycle_id) THEN
        RETURN NULL;
    END IF;
    IF (SELECT COALESCE(SUM(amount_paise), 0) FROM rent_disbursement
         WHERE cycle_id = NEW.cycle_id)
       > (SELECT total_rent_paise FROM rent_cycle WHERE cycle_id = NEW.cycle_id) THEN
        RAISE EXCEPTION 'overpaid_rent: the landlord payouts would exceed the month''s rent';
    END IF;
    RETURN NULL;
END;
$$;
