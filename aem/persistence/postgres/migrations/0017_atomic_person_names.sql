-- People's names in parts: first, middle and last.
--
-- A contact's name and the lease's landlord were one free-text field each.
-- They are now collected as a first name, an optional middle name and an
-- optional last name -- optional because many Indian names, on PAN and Aadhaar
-- too, have no surname. The parts are what is edited. name and landlord_name
-- stay, composed from the parts on every write, so everything that reads one
-- name -- passbooks, receipts, joins, the audit log -- keeps working.
--
-- Names already on file are split the way the application splits a whole name
-- it is sent: the first word, the last word, and the words between, words
-- being separated by any run of whitespace.

ALTER TABLE household_contact
    ADD COLUMN first_name TEXT CHECK (first_name IS NULL OR length(trim(first_name)) > 0),
    ADD COLUMN middle_name TEXT CHECK (middle_name IS NULL OR length(trim(middle_name)) > 0),
    ADD COLUMN last_name TEXT CHECK (last_name IS NULL OR length(trim(last_name)) > 0);

UPDATE household_contact AS contact
   SET first_name = split.words[1],
       middle_name = CASE WHEN cardinality(split.words) > 2
                          THEN array_to_string(split.words[2:cardinality(split.words) - 1], ' ')
                     END,
       last_name = CASE WHEN cardinality(split.words) > 1
                        THEN split.words[cardinality(split.words)]
                   END
  FROM (SELECT contact_id,
               regexp_split_to_array(regexp_replace(name, '^\s+|\s+$', '', 'g'), '\s+') AS words
          FROM household_contact
         WHERE name ~ '\S') AS split
 WHERE split.contact_id = contact.contact_id
   AND contact.first_name IS NULL;

ALTER TABLE rent_lease_config
    ADD COLUMN landlord_first_name TEXT
        CHECK (landlord_first_name IS NULL OR length(trim(landlord_first_name)) > 0),
    ADD COLUMN landlord_middle_name TEXT
        CHECK (landlord_middle_name IS NULL OR length(trim(landlord_middle_name)) > 0),
    ADD COLUMN landlord_last_name TEXT
        CHECK (landlord_last_name IS NULL OR length(trim(landlord_last_name)) > 0);

UPDATE rent_lease_config AS lease
   SET landlord_first_name = split.words[1],
       landlord_middle_name = CASE WHEN cardinality(split.words) > 2
                                   THEN array_to_string(
                                            split.words[2:cardinality(split.words) - 1], ' ')
                              END,
       landlord_last_name = CASE WHEN cardinality(split.words) > 1
                                 THEN split.words[cardinality(split.words)]
                            END
  FROM (SELECT config_id,
               regexp_split_to_array(regexp_replace(landlord_name, '^\s+|\s+$', '', 'g'),
                                     '\s+') AS words
          FROM rent_lease_config
         WHERE landlord_name ~ '\S') AS split
 WHERE split.config_id = lease.config_id
   AND lease.landlord_first_name IS NULL;
