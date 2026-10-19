-- 0021_resident_dossier_extensions.sql: Complete resident dossier attributes.
-- Enforces Rule D1: Strict Field Atomicity.
--
-- Who a resident is beyond their contact details: work, medical safety, the
-- terms they moved in on, their vehicle and building access, and identity
-- verification. Every column is optional or starts at a default, and none of
-- it is read by the ledger.
ALTER TABLE resident
    -- 1. Professional & Work Mode
    ADD COLUMN profession TEXT,
    ADD COLUMN employer TEXT,
    ADD COLUMN work_mode TEXT CHECK (work_mode IS NULL OR work_mode IN ('wfh', 'hybrid', 'onsite')),
    -- 2. Health & Medical Safety
    ADD COLUMN blood_group TEXT CHECK (blood_group IS NULL OR blood_group IN ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-')),
    ADD COLUMN secondary_emergency_name TEXT,
    ADD COLUMN secondary_emergency_phone TEXT,
    ADD COLUMN secondary_emergency_relation TEXT,
    -- 3. Tenancy Agreement Terms
    ADD COLUMN notice_period_days INTEGER CHECK (notice_period_days IS NULL OR notice_period_days >= 0),
    ADD COLUMN lock_in_until TEXT,
    ADD COLUMN is_lease_signatory INTEGER NOT NULL DEFAULT 0 CHECK (is_lease_signatory IN (0, 1)),
    ADD COLUMN keys_issued_notes TEXT,
    -- 4. Building & Vehicle Logistics
    ADD COLUMN vehicle_type TEXT CHECK (vehicle_type IS NULL OR vehicle_type IN ('none', 'two_wheeler', 'four_wheeler', 'both')),
    ADD COLUMN vehicle_reg_no TEXT,
    ADD COLUMN parking_slot TEXT,
    ADD COLUMN society_rfid_tag TEXT,
    -- 5. KYC & Legal Verification
    ADD COLUMN id_document_type TEXT CHECK (id_document_type IS NULL OR id_document_type IN ('aadhaar', 'passport', 'voter_id', 'driving_license', 'other')),
    ADD COLUMN id_document_number TEXT,
    ADD COLUMN home_city TEXT,
    ADD COLUMN home_state TEXT,
    ADD COLUMN police_verification_status TEXT NOT NULL DEFAULT 'pending' CHECK (police_verification_status IN ('pending', 'submitted', 'verified', 'exempt')),
    ADD COLUMN society_noc_status TEXT NOT NULL DEFAULT 'pending' CHECK (society_noc_status IN ('pending', 'approved', 'not_applicable'));
