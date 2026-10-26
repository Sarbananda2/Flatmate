-- Deleting a transaction recorded by mistake, while its month is open.
--
-- A payment into or out of the kitty, or a payout to the landlord or a
-- provider, entered wrongly -- the wrong amount, the wrong resident, twice --
-- can be deleted outright while its month's statement is still a draft (or
-- not created yet). The service layer deletes whatever was recorded together
-- in one transaction and writes each deleted row to the audit trail
-- (aem/services/deletions.py). Once the month's statement is finalized its
-- transactions are locked; for payouts this is held here as well, so it holds
-- whoever asks.

-- A payout may be deleted only while its month's statement is open.
DROP TRIGGER trg_rent_disbursement_no_delete ON rent_disbursement;
DROP TRIGGER trg_service_disbursement_no_delete ON service_disbursement;

CREATE FUNCTION aem_payout_deletable_in_open_month() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF COALESCE((SELECT status FROM monthly_statement
                  WHERE apartment_id = OLD.apartment_id
                    AND settlement_month = OLD.settlement_month), 'draft') <> 'draft' THEN
        RAISE EXCEPTION 'locked_statement: a payout in a finalized month cannot be deleted';
    END IF;
    RETURN OLD;
END;
$$;

CREATE TRIGGER trg_rent_disbursement_open_month_delete
BEFORE DELETE ON rent_disbursement FOR EACH ROW
EXECUTE FUNCTION aem_payout_deletable_in_open_month();

CREATE TRIGGER trg_service_disbursement_open_month_delete
BEFORE DELETE ON service_disbursement FOR EACH ROW
EXECUTE FUNCTION aem_payout_deletable_in_open_month();

-- Payments never had a delete guard of their own; should one exist, it goes.
DROP TRIGGER IF EXISTS trg_payment_no_delete ON payment;

-- A month paid to the landlord in full stays as it is -- unless one of its
-- payouts has been deleted, leaving the landlord owed again.
DROP TRIGGER trg_rent_cycle_disbursed_no_update ON rent_cycle;

CREATE FUNCTION aem_rent_cycle_disbursed_no_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (SELECT COALESCE(SUM(amount_paise), 0) FROM rent_disbursement
         WHERE cycle_id = OLD.cycle_id) >= OLD.total_rent_paise THEN
        RAISE EXCEPTION 'append_only: a disbursed rent cycle cannot be changed';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_rent_cycle_disbursed_no_update
BEFORE UPDATE ON rent_cycle FOR EACH ROW
WHEN (OLD.disbursement_status = 'disbursed')
EXECUTE FUNCTION aem_rent_cycle_disbursed_no_update();
