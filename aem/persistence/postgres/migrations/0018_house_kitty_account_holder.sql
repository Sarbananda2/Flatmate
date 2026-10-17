-- Whose account the House Kitty is held in.
--
-- The household pool is one accounting entity, the House Kitty, on every
-- screen and statement. Its money sits in a real person's bank account or UPI
-- ID -- the primary tenant's, or a roommate's -- and that person's name is what
-- a payer's banking app shows. pool_account_holder records it, and pool_name
-- becomes the fixed 'House Kitty', its default from now on.
--
-- A pool name that had been changed from the defaults was most likely that
-- person's name, so it is kept as the account holder.

ALTER TABLE apartment
    ADD COLUMN pool_account_holder TEXT
        CHECK (pool_account_holder IS NULL OR length(trim(pool_account_holder)) > 0);

UPDATE apartment
   SET pool_account_holder = CASE WHEN pool_name NOT IN ('Household Pool', 'House Kitty')
                                  THEN pool_name
                             END,
       pool_name = 'House Kitty';

ALTER TABLE apartment ALTER COLUMN pool_name SET DEFAULT 'House Kitty';
