"""Platform users: the household admin's sign-up, logging in, and sessions.

Passwords are hashed with scrypt from the standard library, so there is no
third-party cryptography to install or keep patched. A session is a random
token handed to the browser in a cookie; the database keeps only its SHA-256,
so a copy of the sessions table cannot be used to sign in.

The first person to sign up becomes the household admin. Signing up is closed
once anyone has: later users are added by the admin, not by themselves.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Mapping

from ..errors import AuthenticationError, ConflictError, ValidationError
from ..ids import new_id, now_timestamp
from ..persistence import Database, UnitOfWork
from . import audit
from .common import require_text

SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1

MIN_PASSWORD_LENGTH = 8
# Long enough for any passphrase; short enough that hashing one stays cheap.
MAX_PASSWORD_LENGTH = 1024

SESSION_LIFETIME = timedelta(days=30)
# A session in use is renewed once it has less than this left, so someone who
# uses the app at least every couple of weeks stays signed in.
RENEW_WHEN_LESS_THAN = timedelta(days=15)

HOUSEHOLD_ADMIN = "household_admin"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
INVALID_CREDENTIALS = "Invalid email or password."


# ------------------------------------------------------------------ passwords --

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                             n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, derived_hex = hashed.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(derived_hex)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


@lru_cache(maxsize=1)
def _decoy_hash() -> str:
    """Checked against when no user has the email, so an unknown address takes
    as long to refuse as a wrong password and the timing does not tell them apart."""
    return hash_password(secrets.token_urlsafe(16))


# --------------------------------------------------------------------- inputs --

def normalize_email(value: Any) -> str:
    email = require_text(value, "email", max_length=254).lower()
    if not _EMAIL_RE.match(email):
        raise ValidationError("Enter a valid email address, like name@example.com.",
                              code="invalid_email", details={"field": "email"})
    return email


def _check_password(password: Any) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"The password must be at least {MIN_PASSWORD_LENGTH} characters.",
            code="weak_password", details={"field": "password"})
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValidationError(
            f"The password must be at most {MAX_PASSWORD_LENGTH} characters.",
            code="invalid_password", details={"field": "password"})
    return password


def public_user(user: Mapping[str, Any]) -> dict[str, Any]:
    """What the API says about a user: never the password hash."""
    return {"user_id": user["user_id"], "email": user["email"],
            "full_name": user["full_name"], "role": user["role"]}


# ------------------------------------------------------------------- sessions --

def session_key(token: str) -> str:
    """What the database stores for a session token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _start_session(uow: UnitOfWork, user_id: str, *, ip_address: str | None,
                   user_agent: str | None) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now().astimezone()
    uow.auth.insert_session(
        session_id=session_key(token), user_id=user_id, created_at=now_timestamp(now),
        expires_at=now_timestamp(now + SESSION_LIFETIME),
        ip_address=(ip_address or None) and ip_address[:64],
        user_agent=(user_agent or None) and user_agent[:500])
    return token


# ------------------------------------------------------------------- services --

def get_auth_status(db: Database) -> dict[str, Any]:
    with db.read() as uow:
        count = uow.auth.user_count()
    return {"setup_required": count == 0, "user_count": count}


def register_household_admin(db: Database, *, email: Any, password: Any, full_name: Any,
                             ip_address: str | None = None,
                             user_agent: str | None = None) -> tuple[dict[str, Any], str]:
    """Sign up the first user, as the household admin, and sign them in.

    Every apartment already on record is put in their charge. Returns the user
    and a session token.
    """
    email = normalize_email(email)
    full_name = require_text(full_name, "full_name", max_length=200)
    password_hash = hash_password(_check_password(password))
    with db.transaction() as uow:
        # Write transactions are serialised, so two sign-ups cannot both see none.
        if uow.auth.user_count() > 0:
            raise ConflictError(
                "A household admin has already signed up. Log in instead.",
                code="signup_closed")
        user_id = new_id("usr")
        timestamp = now_timestamp()
        uow.auth.insert_user(user_id=user_id, email=email, full_name=full_name,
                             password_hash=password_hash, role=HOUSEHOLD_ADMIN,
                             created_at=timestamp, updated_at=timestamp)
        for apartment_id in uow.auth.apartments_without_admin():
            uow.auth.link_apartment_admin(apartment_id, user_id)
        audit.record(uow, entity_type="app_user", entity_id=user_id, action="create",
                     actor=full_name,
                     new_value={"email": email, "full_name": full_name, "role": HOUSEHOLD_ADMIN})
        token = _start_session(uow, user_id, ip_address=ip_address, user_agent=user_agent)
        user = {"user_id": user_id, "email": email, "full_name": full_name,
                "role": HOUSEHOLD_ADMIN}
    return user, token


def authenticate(db: Database, *, email: Any, password: Any, ip_address: str | None = None,
                 user_agent: str | None = None) -> tuple[dict[str, Any], str]:
    """Log a user in. Returns the user and a session token.

    Every refusal says the same thing, so it does not reveal which emails have
    an account.
    """
    try:
        normalized = normalize_email(email)
    except ValidationError:
        normalized = None
    with db.read() as uow:
        user = uow.auth.get_user_by_email(normalized) if normalized else None
    matches = verify_password(password if isinstance(password, str) else "",
                              user["password_hash"] if user else _decoy_hash())
    if not (user and matches and user["is_active"]):
        raise AuthenticationError(INVALID_CREDENTIALS, code="invalid_credentials")
    with db.transaction() as uow:
        uow.auth.cleanup_expired_sessions(now_timestamp())
        token = _start_session(uow, user["user_id"], ip_address=ip_address,
                               user_agent=user_agent)
    return public_user(user), token


def validate_session(db: Database, token: str | None) -> dict[str, Any] | None:
    """The signed-in user for this token, or ``None`` when it is not a live session."""
    if not token or len(token) > 256:
        return None
    key = session_key(token)
    with db.read() as uow:
        session = uow.auth.get_session(key)
    if session is None or not session["is_active"]:
        return None
    now = datetime.now().astimezone()
    expires_at = datetime.fromisoformat(session["expires_at"])
    if expires_at <= now:
        return None
    if expires_at - now < RENEW_WHEN_LESS_THAN:
        with db.transaction() as uow:
            uow.auth.extend_session(key, now_timestamp(now + SESSION_LIFETIME))
    return public_user(session)


def logout(db: Database, token: str | None) -> None:
    if not token or len(token) > 256:
        return
    with db.transaction() as uow:
        uow.auth.delete_session(session_key(token))
