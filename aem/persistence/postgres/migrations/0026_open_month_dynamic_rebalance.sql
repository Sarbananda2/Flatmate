-- An open month's bill follows its configuration, whoever has paid.
--
-- While a month's statement is a draft, the rent (the lease, the split, the
-- rooms) and a service's bill (the items rented) can change after residents
-- have paid their shares -- into the House Kitty, or to the landlord or the
-- provider directly -- and after the landlord or provider has been paid. The
-- service layer moves what each resident paid to their new share: a payment
-- into the kitty settles up to the share and the rest is owed back to them;
-- a resident's own payout is split again into their share, paid through the
-- kitty, and the extra the kitty owes them (aem/services/bill_shares.py). It
-- still refuses a bill below what the landlord or provider has been paid.
--
-- Once the month's statement is finalized, all of it is locked, as before.

-- A month paid to the landlord in full may still change while it is open.
DROP TRIGGER trg_rent_cycle_disbursed_no_update ON rent_cycle;
DROP FUNCTION aem_rent_cycle_disbursed_no_update();

CREATE FUNCTION aem_rent_cycle_disbursed_no_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF COALESCE((SELECT status FROM monthly_statement
                  WHERE apartment_id = OLD.apartment_id
                    AND settlement_month = OLD.settlement_month), 'draft') <> 'draft' THEN
        RAISE EXCEPTION 'append_only: a disbursed rent cycle in a finalized month cannot be changed';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_rent_cycle_disbursed_no_update
BEFORE UPDATE ON rent_cycle FOR EACH ROW
WHEN (OLD.disbursement_status = 'disbursed')
EXECUTE FUNCTION aem_rent_cycle_disbursed_no_update();

-- A resident's payout keeps its total, but the split of it between their share
-- (paid through the kitty) and their advance follows the share: only the
-- amount of a payout may change, and only while its month is open. Payouts
-- still never add up to more than the month's rent or the bill.
DROP TRIGGER trg_rent_disbursement_no_update ON rent_disbursement;
DROP TRIGGER trg_service_disbursement_no_update ON service_disbursement;

CREATE FUNCTION aem_payout_amount_updatable_in_open_month() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'amount_paise') IS DISTINCT FROM (to_jsonb(OLD) - 'amount_paise') THEN
        RAISE EXCEPTION 'append_only: only the amount of a payout can be changed';
    END IF;
    IF COALESCE((SELECT status FROM monthly_statement
                  WHERE apartment_id = OLD.apartment_id
                    AND settlement_month = OLD.settlement_month), 'draft') <> 'draft' THEN
        RAISE EXCEPTION 'locked_statement: a payout in a finalized month cannot be changed';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_rent_disbursement_open_month_update
BEFORE UPDATE ON rent_disbursement FOR EACH ROW
EXECUTE FUNCTION aem_payout_amount_updatable_in_open_month();

CREATE TRIGGER trg_service_disbursement_open_month_update
BEFORE UPDATE ON service_disbursement FOR EACH ROW
EXECUTE FUNCTION aem_payout_amount_updatable_in_open_month();

CREATE TRIGGER trg_rent_disbursement_update_within_rent
AFTER UPDATE ON rent_disbursement FOR EACH ROW
EXECUTE FUNCTION aem_rent_disbursement_within_rent();

CREATE TRIGGER trg_service_disbursement_update_within_bill
AFTER UPDATE ON service_disbursement FOR EACH ROW
EXECUTE FUNCTION aem_service_disbursement_within_bill();

-- A charge is never settled beyond its own amount, however it is settled: an
-- application topped up later is held to it as a new one is.
CREATE TRIGGER trg_payment_application_update_within_allocation
AFTER UPDATE ON payment_application FOR EACH ROW
WHEN (NEW.allocation_id IS NOT NULL)
EXECUTE FUNCTION aem_application_within_allocation();
