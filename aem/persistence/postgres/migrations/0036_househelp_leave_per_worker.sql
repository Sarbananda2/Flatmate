-- Leave is the worker's, not each role's. A worker away for a day is away from
-- every role they do, so the days off they may take a month before pay is
-- deducted are theirs; each role's pay for a day beyond them is deducted pro
-- rata at that role's salary (aem/engine/househelp.py).
--
-- A worker takes the largest allowance any of their roles had, or two days.

ALTER TABLE househelp_worker
    ADD COLUMN allowed_leaves_per_month INT NOT NULL DEFAULT 2
        CHECK (allowed_leaves_per_month >= 0);

UPDATE househelp_worker w
   SET allowed_leaves_per_month = r.allowed
  FROM (SELECT worker_id, MAX(allowed_leaves_per_month) AS allowed
          FROM househelp_role
         WHERE worker_id IS NOT NULL
         GROUP BY worker_id) r
 WHERE r.worker_id = w.worker_id;

ALTER TABLE househelp_role DROP COLUMN allowed_leaves_per_month;
