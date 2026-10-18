-- Platform users, their sessions, and the household admin.
--
-- The first person to sign up becomes the household admin: they manage the
-- apartment and act for every resident seat, and the audit log names them as
-- the actor. A resident seat can later be linked to a user of its own.
--
-- An email is stored trimmed and in lower case, so it is unique however it was
-- typed. A password is stored only as its scrypt hash. A session is stored as
-- the SHA-256 of its token, never the token itself, so reading this table does
-- not let anyone sign in.

CREATE TABLE app_user (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE
        CHECK (length(email) BETWEEN 3 AND 254 AND email = lower(btrim(email))),
    full_name TEXT NOT NULL CHECK (length(btrim(full_name)) > 0),
    password_hash TEXT NOT NULL CHECK (password_hash LIKE 'scrypt$%'),
    role TEXT NOT NULL DEFAULT 'household_admin'
        CHECK (role IN ('household_admin', 'resident', 'auditor')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE user_session (
    session_id TEXT PRIMARY KEY CHECK (session_id ~ '^[0-9a-f]{64}$'),
    user_id TEXT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT
);

CREATE INDEX idx_user_session_user_id ON user_session(user_id);
CREATE INDEX idx_user_session_expires_at ON user_session(expires_at);

-- The household admin who manages the apartment.
ALTER TABLE apartment
    ADD COLUMN admin_user_id TEXT REFERENCES app_user(user_id);

-- The platform user a resident seat belongs to, once they have an account.
ALTER TABLE resident
    ADD COLUMN user_id TEXT REFERENCES app_user(user_id);
