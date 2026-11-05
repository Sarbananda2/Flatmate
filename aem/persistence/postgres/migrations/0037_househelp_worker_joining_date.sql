-- The day each house help worker joined. A month before it is not theirs: their
-- roles are on no month that ends before they joined. The month they joined in
-- pays each role for the days from that day on, its salary pro rata
-- (aem/engine/househelp.py).
--
-- Workers already on file are taken to have joined on 1 January 2026, so each
-- month they were paid in stays paid in full; set each one's day on the Staff
-- tab.

ALTER TABLE househelp_worker
    ADD COLUMN joined_date TEXT NOT NULL DEFAULT '2026-01-01'
        CHECK (joined_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$');
