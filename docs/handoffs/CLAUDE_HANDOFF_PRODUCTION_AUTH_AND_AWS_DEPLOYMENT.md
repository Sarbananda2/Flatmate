# Architectural Specification: Household Admin User & Production AWS Hosting

**Role:** Lead Architect & Domain Modeler  
**Audience:** Claude Code (Lead Implementer)  
**Status:** Approved for Implementation  
**Governing Principles:** Platform User & Household Admin Domain Model, Zero-Friction First-User Sign-up, Standard-Library Cryptography, Seamless AWS Production Containerization.  
**Files Affected:**
- Backend:
  - `aem/persistence/postgres/migrations/0019_user_authentication_and_household_admin.sql` (New)
  - `aem/persistence/repositories/auth.py` (New)
  - `aem/persistence/repositories/__init__.py`
  - `aem/persistence/unit_of_work.py`
  - `aem/services/auth.py` (New)
  - `aem/api/routers/auth.py` (New)
  - `aem/api/routers/__init__.py`
  - `aem/api/deps.py`
  - `aem/main.py`
- Frontend:
  - `frontend/src/context/AuthContext.tsx` (New)
  - `frontend/src/pages/auth/Login.tsx` (New)
  - `frontend/src/pages/auth/SignupAdmin.tsx` (New)
  - `frontend/src/components/Layout.tsx`
  - `frontend/src/App.tsx`
- Production & AWS Deployment:
  - `Dockerfile` (Multi-stage build)
  - `.dockerignore`
  - `docker-compose.prod.yml`
  - `Caddyfile`
  - `deploy/aws-user-data.sh`
  - `.env.production.example`

---

## 1. Domain Concept: The Household Admin User

We are **not** creating a technical "operator" utility account. We are introducing real **Platform Users**:
- The first user who signs up on the platform becomes the **Household Admin**.
- The Household Admin has full administrative and ledger authority over their apartment/household (managing residents, rooms, categories, rent, electricity, and payments).
- Resident profiles in the household represent "seats" (occupants). The Household Admin can CRUD transactions and balances for any resident seat, even before other roommates have their own accounts.
- In audit logs and transaction traces, the `actor` is now the actual logged-in user (e.g. `"Sarbananda Taw"`), rather than a static string `"operator"`.

---

## 2. Database Schema (`0019_user_authentication_and_household_admin.sql`)

```sql
-- 0019_user_authentication_and_household_admin.sql
-- User accounts, session management, and household admin association.

CREATE TABLE IF NOT EXISTS app_user (
    user_id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL CHECK (length(trim(email)) >= 3),
    full_name TEXT NOT NULL CHECK (length(trim(full_name)) > 0),
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'household_admin' CHECK (role IN ('household_admin', 'resident', 'auditor')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_session (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip_address TEXT,
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_user_session_user_id ON user_session(user_id);
CREATE INDEX IF NOT EXISTS idx_user_session_expires_at ON user_session(expires_at);

-- Link household admin to the apartment
ALTER TABLE apartment
    ADD COLUMN admin_user_id TEXT REFERENCES app_user(user_id);

-- Optional link on resident seat to a registered platform user
ALTER TABLE resident
    ADD COLUMN user_id TEXT REFERENCES app_user(user_id);
```

---

## 3. Backend Architecture

### 3.1. Persistence Layer (`aem/persistence/repositories/auth.py`)
Add `AuthRepository`:
- `user_count() -> int`: `SELECT COUNT(*) FROM app_user`.
- `get_user_by_id(user_id: str) -> dict | None`
- `get_user_by_email(email: str) -> dict | None`
- `insert_user(user_id, email, full_name, password_hash, role, created_at, updated_at)`
- `link_apartment_admin(apartment_id: str, user_id: str)`: Associates user as `admin_user_id`.
- `insert_session(session_id, user_id, created_at, expires_at, ip_address, user_agent)`
- `get_session(session_id: str) -> dict | None`: Joins `app_user` to return session + user info.
- `delete_session(session_id: str) -> None`
- `cleanup_expired_sessions(now_ts: str) -> int`

Register `auth` on `UnitOfWork` (`aem/persistence/unit_of_work.py`).

### 3.2. Authentication Service (`aem/services/auth.py`)
Password security using Python standard library `hashlib.scrypt` (NIST/OWASP approved, zero external C-dependencies):
```python
import hashlib
import hmac
import secrets
from typing import Any
from ..ids import now_timestamp, uuid7
from ..errors import AuthenticationError, ConflictError, ValidationError

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${dk.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    try:
        scheme, n_str, r_str, p_str, salt_hex, dk_hex = hashed.split('$')
        if scheme != 'scrypt':
            return False
        salt = bytes.fromhex(salt_hex)
        expected_dk = bytes.fromhex(dk_hex)
        actual_dk = hashlib.scrypt(
            password.encode('utf-8'), salt=salt,
            n=int(n_str), r=int(r_str), p=int(p_str)
        )
        return hmac.compare_digest(actual_dk, expected_dk)
    except Exception:
        return False
```

Service Methods:
- `get_auth_status(db: Database) -> dict[str, Any]`:
  Returns `{"setup_required": user_count == 0, "user_count": user_count}`.
- `register_household_admin(db, *, email, password, full_name, ip_address, user_agent) -> tuple[dict, str]`:
  - If `user_count > 0`, checks if registration is restricted (initial user bootstrap only).
  - Validates email format and password length (>= 8 chars).
  - Inserts user with `role = 'household_admin'`.
  - Links user as `admin_user_id` for existing default apartment if present.
  - Creates a 30-day rolling session (`session_id = secrets.token_urlsafe(32)`).
  - Returns `(user_dict, session_id)`.
- `authenticate(db, *, email, password, ip_address, user_agent) -> tuple[dict, str]`:
  - Normalizes email, verifies scrypt hash.
  - Returns `(user_dict, session_id)` or raises `AuthenticationError("Invalid email or password.")`.
- `logout(db, session_id: str) -> None`:
  - Removes session.
- `validate_session(db, session_id: str) -> dict[str, Any] | None`:
  - Returns active user profile if session exists and is unexpired.

### 3.3. API Endpoints (`aem/api/routers/auth.py`)
- `GET /api/auth/status`: Public. Returns `{ setup_required, authenticated, user }`.
- `POST /api/auth/signup`: Public (only when `setup_required: true`). Creates Household Admin user, sets `aem_session` HTTP-only cookie, returns `{ user }`.
- `POST /api/auth/login`: Public. Validates credentials, sets `aem_session` cookie, returns `{ user }`.
- `POST /api/auth/logout`: Clears `aem_session` cookie.
- `GET /api/auth/me`: Returns current authenticated user.

### 3.4. Dependency Protection (`aem/api/deps.py`)
Define `CurrentUser`:
- Extracts cookie `aem_session` (or `Authorization: Bearer <token>`).
- Validates session with `validate_session`. If invalid or missing, raises `HTTPException(401, detail="Authentication required")`.
- Dynamic actor resolution: Instead of hardcoded `"operator"`, endpoints use `actor = current_user["full_name"]` or `current_user["email"]`.
- Public routes: `/health`, `/api/auth/status`, `/api/auth/signup`, `/api/auth/login`, static assets (`/assets/*`). All other `/api/*` routes require `CurrentUser`.

---

## 4. Frontend Experience: Household Admin Flow

### 4.1. `AuthContext.tsx` (`frontend/src/context/AuthContext.tsx`)
Create an authentication provider wrapping `<Layout>`:
```tsx
interface AuthContextValue {
  user: User | null
  setupRequired: boolean
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  signupAdmin: (data: { email: string; fullName: string; password: string }) => Promise<void>
}
```

### 4.2. Routing & Views in `App.tsx`
1. **Initial Bootstrap (`setup_required: true`):**
   - Redirect to `/signup` ("Create Household Admin Account").
   - Title: **"Create your Household Admin Account"**
   - Subtitle: *"Set up your account to manage your apartment, residents, and shared expenses."*
   - Inputs: Full Name (e.g. *Sarbananda Taw*), Email address, Password.
   - On submit, automatically signs in and transitions directly to the Guided Setup Wizard (`/welcome`) or Dashboard.
2. **Standard Authentication (`setup_required: false` && unauthenticated):**
   - Redirect to `/login` ("Log in to your Household").
   - Inputs: Email & Password.
3. **In-App Top Navigation (`Layout.tsx`):**
   - Replace generic ribbon indicators with a clean user pill:
     - Avatar circle with initials (e.g. `ST`).
     - Name: `Sarbananda Taw`
     - Badge: `Admin`
     - Dropdown / button: `Log out`.

---

## 5. Production AWS Deployment Verification

The container stack has been created and verified:
- [`Dockerfile`](file:///home/sarbanandataw/Projects/apartment_expense_manager_prototype/Dockerfile): Node 20 builds React frontend, Python 3.12 runs FastAPI and migrations.
- [`docker-compose.prod.yml`](file:///home/sarbanandataw/Projects/apartment_expense_manager_prototype/docker-compose.prod.yml): Orchestrates PostgreSQL 16 + AEM API + Caddy.
- [`Caddyfile`](file:///home/sarbanandataw/Projects/apartment_expense_manager_prototype/Caddyfile): Automated free Let's Encrypt SSL/HTTPS.
- [`.env.production.example`](file:///home/sarbanandataw/Projects/apartment_expense_manager_prototype/.env.production.example): Production environment template.
- [`deploy/aws-user-data.sh`](file:///home/sarbanandataw/Projects/apartment_expense_manager_prototype/deploy/aws-user-data.sh): Automated Docker install for EC2/Lightsail.

---

## 6. Implementation Verification Checklist

1. **Migration Execution:** Run `scripts/manage.py init` to ensure `0019_user_authentication_and_household_admin.sql` executes smoothly.
2. **First-User Signup:** Verify navigating to `/` on a fresh database routes to `/signup`, successfully creates the Household Admin, and issues the `aem_session` cookie.
3. **Session Persistence:** Verify refreshing the page maintains authentication without flicker.
4. **Audit Actor:** Verify that recording an expense or editing a resident records the Household Admin's name in the `audit` table instead of `"operator"`.
5. **Existing Tests:** Ensure all existing pytest and vitest suites continue to pass.
