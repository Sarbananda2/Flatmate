-- A house help role may be set up before anyone is hired to do it: cleaning,
-- cooking, each on its salary, leave allowance and split, with the worker
-- given later. A role with no worker joins no month -- there is nobody to pay
-- -- and once a month has been saved with a role, its worker stays
-- (aem/services/househelp.py).

ALTER TABLE househelp_role ALTER COLUMN worker_id DROP NOT NULL;
