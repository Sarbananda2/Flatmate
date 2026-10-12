-- Lease contract term, notice window, and renewal history.
--
-- A master lease runs for a fixed term (commonly 11 months). These columns say
-- when the current term starts and ends, how much notice the landlord needs,
-- what deposit is held, and how the rent escalates on renewal. Nothing here
-- gates rent: a term that has run out never stops a month's rent being
-- generated.
--
-- Money columns are INTEGER paise, with typeof() checked as in 0005.

ALTER TABLE rent_lease_config ADD COLUMN lease_start_date TEXT;      -- YYYY-MM-DD
ALTER TABLE rent_lease_config ADD COLUMN lease_end_date TEXT;        -- YYYY-MM-DD, inclusive
ALTER TABLE rent_lease_config ADD COLUMN duration_months INTEGER DEFAULT 11
    CHECK (duration_months > 0);
ALTER TABLE rent_lease_config ADD COLUMN notice_period_days INTEGER DEFAULT 30
    CHECK (notice_period_days >= 0);
ALTER TABLE rent_lease_config ADD COLUMN lock_in_months INTEGER DEFAULT 6
    CHECK (lock_in_months >= 0);
ALTER TABLE rent_lease_config ADD COLUMN security_deposit_paise INTEGER
    CHECK (security_deposit_paise IS NULL
           OR (typeof(security_deposit_paise) = 'integer' AND security_deposit_paise >= 0));
ALTER TABLE rent_lease_config ADD COLUMN escalation_type TEXT DEFAULT 'percent'
    CHECK (escalation_type IN ('percent', 'fixed', 'none'));
ALTER TABLE rent_lease_config ADD COLUMN escalation_percent REAL DEFAULT 5.0;
-- The agreed rent for the next term, when escalation_type is 'fixed'.
ALTER TABLE rent_lease_config ADD COLUMN renewal_rent_paise INTEGER
    CHECK (renewal_rent_paise IS NULL
           OR (typeof(renewal_rent_paise) = 'integer' AND renewal_rent_paise > 0));
ALTER TABLE rent_lease_config ADD COLUMN lease_status TEXT DEFAULT 'active'
    CHECK (lease_status IN ('active', 'notice_served', 'vacated', 'renewed'));
ALTER TABLE rent_lease_config ADD COLUMN notice_served_date TEXT;    -- YYYY-MM-DD
ALTER TABLE rent_lease_config ADD COLUMN target_handover_date TEXT;  -- YYYY-MM-DD

-- Completed terms, one row each, written when a lease is renewed. The current
-- term lives on rent_lease_config; its number is one more than the rows here.
CREATE TABLE IF NOT EXISTS rent_lease_term (
    term_id TEXT PRIMARY KEY,
    apartment_id TEXT NOT NULL REFERENCES apartment(apartment_id),
    term_number INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    monthly_rent_paise INTEGER NOT NULL
        CHECK (typeof(monthly_rent_paise) = 'integer' AND monthly_rent_paise > 0),
    -- The change from this term's rent to the next term's, as a percentage.
    escalation_applied_percent REAL,
    archived_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(apartment_id, term_number)
);
