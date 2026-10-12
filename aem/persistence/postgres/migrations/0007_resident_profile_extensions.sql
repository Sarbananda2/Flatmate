-- Resident profile: an emergency contact.
--
-- Who to call for a resident if something goes wrong: a name, how they are
-- related, and a phone number. All three are optional free text, kept beside
-- the resident's own phone, email and UPI ID. None of it is used for
-- accounting.

ALTER TABLE resident ADD COLUMN emergency_contact_name TEXT;
ALTER TABLE resident ADD COLUMN emergency_contact_phone TEXT;
ALTER TABLE resident ADD COLUMN emergency_contact_relation TEXT;
