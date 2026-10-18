"""The household admin's account, logging in, and sessions (migration 0019).

The first person to sign up is the household admin; signing up closes after
that. Every route but /health and /api/auth/* needs a signed-in user, and the
audit log names that user rather than a fixed "operator".
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

import pytest

from aem.services import apartments, auth
from tests.support import ADMIN, connect

LOGIN = {"email": ADMIN["email"], "password": ADMIN["password"]}


@pytest.fixture()
def anon(db):
    """A client that has not signed in."""
    from fastapi.testclient import TestClient

    from aem.config import get_settings
    from aem.main import create_app

    with TestClient(create_app(get_settings(), database=db)) as test_client:
        yield test_client


def _rows(db, sql, params=()):
    with connect(db, read_only=True) as conn:
        return conn.execute(sql, params).fetchall()


def _signup(client, **overrides):
    return client.post("/api/auth/signup", json={**ADMIN, **overrides})


# ------------------------------------------------------------------ passwords --

def test_passwords_are_hashed_with_scrypt_at_the_real_cost(monkeypatch):
    monkeypatch.setattr(auth, "SCRYPT_N", 16384)
    hashed = auth.hash_password("correct horse battery")
    scheme, n, r, p, salt, derived = hashed.split("$")
    assert (scheme, n, r, p) == ("scrypt", "16384", "8", "1")
    assert len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(derived)) == 64
    assert "correct horse battery" not in hashed
    assert auth.verify_password("correct horse battery", hashed)
    assert not auth.verify_password("correct horse batterY", hashed)
    # The same password hashes differently each time: the salt is random.
    assert auth.hash_password("correct horse battery") != hashed


@pytest.mark.parametrize("stored", ["", "plain", "bcrypt$1$2$3$aa$bb", "scrypt$x$8$1$aa$bb"])
def test_a_malformed_hash_never_verifies(stored):
    assert auth.verify_password("anything", stored) is False


# -------------------------------------------------------------------- signing up --

def test_a_new_database_asks_for_the_household_admin(anon):
    assert anon.get("/api/auth/status").json() == {
        "setup_required": True, "authenticated": False, "user": None}


def test_the_first_sign_up_is_the_household_admin_and_signs_them_in(anon, db):
    response = _signup(anon)
    assert response.status_code == 201, response.text
    user = response.json()["user"]
    assert user["full_name"] == "Asha Rao" and user["role"] == "household_admin"
    assert user["email"] == "asha@example.com"
    assert "password" not in response.text

    cookie = response.headers["set-cookie"]
    assert cookie.startswith("aem_session=")
    for attribute in ("HttpOnly", "SameSite=lax", "Path=/", "Max-Age=2592000"):
        assert attribute.lower() in cookie.lower(), attribute

    assert anon.get("/api/auth/status").json() == {
        "setup_required": False, "authenticated": True, "user": user}
    assert anon.get("/api/auth/me").json() == {"user": user}
    stored = _rows(db, "SELECT email, role, password_hash FROM app_user")
    assert len(stored) == 1 and stored[0]["password_hash"].startswith("scrypt$")


def test_signing_up_closes_once_someone_has(anon):
    assert _signup(anon).status_code == 201
    again = _signup(anon, email="someone@example.com")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "signup_closed"


@pytest.mark.parametrize("overrides, code", [
    ({"email": "not-an-email"}, "invalid_email"),
    ({"email": "two@at@example.com"}, "invalid_email"),
    ({"password": "short"}, "weak_password"),
    ({"full_name": "   "}, "validation_error"),
])
def test_an_unusable_sign_up_is_refused_and_creates_no_one(anon, db, overrides, code):
    response = _signup(anon, **overrides)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code
    assert _rows(db, "SELECT 1 FROM app_user") == []
    assert anon.get("/api/auth/status").json()["setup_required"] is True


def test_an_email_is_stored_trimmed_and_in_lower_case(anon):
    assert _signup(anon, email="  Asha@Example.COM ").json()["user"]["email"] == "asha@example.com"
    anon.post("/api/auth/logout")
    assert anon.post("/api/auth/login", json={**LOGIN, "email": "ASHA@example.com"}).status_code == 200


# ---------------------------------------------------------------------- logging in --

def test_logging_in_with_the_right_password(anon):
    _signup(anon)
    anon.post("/api/auth/logout")
    response = anon.post("/api/auth/login", json=LOGIN)
    assert response.status_code == 200
    assert response.json()["user"]["full_name"] == "Asha Rao"
    assert response.headers["set-cookie"].startswith("aem_session=")
    assert anon.get("/api/apartments").status_code == 200


@pytest.mark.parametrize("credentials", [
    {"email": ADMIN["email"], "password": "wrong password"},
    {"email": "nobody@example.com", "password": ADMIN["password"]},
    {"email": "not-an-email", "password": ADMIN["password"]},
])
def test_a_refused_login_never_says_which_part_was_wrong(anon, credentials):
    _signup(anon)
    anon.post("/api/auth/logout")
    response = anon.post("/api/auth/login", json=credentials)
    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "invalid_credentials", "message": "Invalid email or password.", "details": {}}
    assert "set-cookie" not in response.headers


def test_a_deactivated_user_cannot_log_in_or_keep_their_session(anon, db):
    _signup(anon)
    with connect(db, as_owner=True) as conn:
        conn.execute("UPDATE app_user SET is_active = FALSE")
    assert anon.get("/api/residents").status_code == 401
    assert anon.post("/api/auth/login", json=LOGIN).status_code == 401


# ------------------------------------------------------------- what needs a user --

def test_private_routes_need_a_signed_in_user(anon):
    for method, path in (("GET", "/api/residents"), ("GET", "/api/apartments"),
                         ("GET", "/api/dashboard"), ("POST", "/api/apartments"),
                         ("GET", "/api/auth/me"), ("GET", "/api/settings")):
        response = anon.request(method, path, json={"apartment_id": "x", "name": "x"})
        assert response.status_code == 401, (method, path, response.status_code)
        assert response.json()["error"]["code"] == "authentication_required"
    # Liveness and the sign-in routes stay open.
    assert anon.get("/health").status_code == 200
    assert anon.get("/api/auth/status").status_code == 200


def test_a_script_can_use_the_session_token_as_a_bearer_token(anon, db):
    from fastapi.testclient import TestClient

    from aem.config import get_settings
    from aem.main import create_app

    _signup(anon)
    token = anon.cookies.get("aem_session")
    with TestClient(create_app(get_settings(), database=db)) as script:
        assert script.get("/api/apartments").status_code == 401
        headers = {"Authorization": f"Bearer {token}"}
        assert script.get("/api/apartments", headers=headers).status_code == 200
        assert script.get("/api/apartments",
                          headers={"Authorization": "Bearer not-a-session"}).status_code == 401


def test_logging_out_ends_the_session(anon, db):
    _signup(anon)
    token = anon.cookies.get("aem_session")
    response = anon.post("/api/auth/logout")
    assert response.status_code == 204
    assert 'aem_session=""' in response.headers["set-cookie"] or "Max-Age=0" in response.headers["set-cookie"]
    assert _rows(db, "SELECT 1 FROM user_session") == []
    assert anon.get("/api/residents", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert anon.get("/api/auth/status").json()["authenticated"] is False


# ------------------------------------------------------------------------- sessions --

def test_only_a_hash_of_the_session_token_is_stored(anon, db):
    _signup(anon)
    token = anon.cookies.get("aem_session")
    stored = [row["session_id"] for row in _rows(db, "SELECT session_id FROM user_session")]
    assert stored == [hashlib.sha256(token.encode()).hexdigest()]
    assert token not in stored


def test_an_expired_session_is_refused(anon, db):
    _signup(anon)
    past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute("UPDATE user_session SET expires_at = %s", (past,))
    assert anon.get("/api/residents").status_code == 401


def test_a_session_in_use_is_renewed_before_it_runs_out(anon, db):
    _signup(anon)
    soon = (datetime.now().astimezone() + timedelta(days=1)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute("UPDATE user_session SET expires_at = %s", (soon,))
    assert anon.get("/api/apartments").status_code == 200
    renewed = datetime.fromisoformat(_rows(db, "SELECT expires_at FROM user_session")[0]["expires_at"])
    assert renewed - datetime.now().astimezone() > timedelta(days=29)


def test_logging_in_clears_away_expired_sessions(anon, db):
    _signup(anon)
    past = (datetime.now().astimezone() - timedelta(days=1)).isoformat(timespec="seconds")
    with connect(db) as conn:
        conn.execute("UPDATE user_session SET expires_at = %s", (past,))
    anon.post("/api/auth/login", json=LOGIN)
    assert len(_rows(db, "SELECT 1 FROM user_session")) == 1


# ------------------------------------------------------------- the household admin --

def test_the_audit_log_names_the_signed_in_user(anon, db):
    _signup(anon)
    assert anon.post("/api/apartments",
                     json={"apartment_id": "home", "name": "Flat 402"}).status_code == 201
    assert anon.post("/api/residents",
                     json={"resident_id": "alice", "display_name": "Alice"}).status_code == 201
    actors = {(row["entity_type"], row["actor"]) for row in _rows(
        db, "SELECT entity_type, actor FROM audit_log")}
    assert ("apartment", "Asha Rao") in actors
    assert ("resident", "Asha Rao") in actors
    assert ("app_user", "Asha Rao") in actors
    assert not any(actor == "operator" for _, actor in actors)


def test_the_audit_log_never_holds_the_password(anon, db):
    _signup(anon)
    for row in _rows(db, "SELECT old_value, new_value FROM audit_log"):
        assert "scrypt$" not in str(row) and ADMIN["password"] not in str(row)


def test_the_household_admin_is_put_in_charge_of_every_apartment(anon, db):
    apartments.create_apartment(db, apartment_id="before", name="Flat 1")
    _signup(anon)
    anon.post("/api/apartments", json={"apartment_id": "after", "name": "Flat 2"})
    admin_id = anon.get("/api/auth/me").json()["user"]["user_id"]
    rows = _rows(db, "SELECT apartment_id, admin_user_id FROM apartment ORDER BY apartment_id")
    assert [(row["apartment_id"], row["admin_user_id"]) for row in rows] == [
        ("after", admin_id), ("before", admin_id)]


def test_a_resident_seat_has_no_user_until_one_is_linked(anon, db):
    _signup(anon)
    anon.post("/api/apartments", json={"apartment_id": "home", "name": "Flat 402"})
    anon.post("/api/residents", json={"resident_id": "alice", "display_name": "Alice"})
    assert [row["user_id"] for row in _rows(db, "SELECT user_id FROM resident")] == [None]
