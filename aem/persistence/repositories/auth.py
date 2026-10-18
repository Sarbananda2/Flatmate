"""Platform users and their sessions."""
from __future__ import annotations

from typing import Any

from .base import Repository

# Everything about a user except the password hash, which only the login path reads.
_USER_COLUMNS = "user_id, email, full_name, role, is_active, created_at, updated_at"


class AuthRepository(Repository):
    def user_count(self) -> int:
        return int(self._s.scalar("SELECT COUNT(*) FROM app_user", (), 0))

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        return self._s.fetch_one(
            f"SELECT {_USER_COLUMNS} FROM app_user WHERE user_id = %s", (user_id,))

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        """The user with this (already normalised) email, with the password hash."""
        return self._s.fetch_one(
            f"SELECT {_USER_COLUMNS}, password_hash FROM app_user WHERE email = %s", (email,))

    def insert_user(self, *, user_id: str, email: str, full_name: str, password_hash: str,
                    role: str, created_at: str, updated_at: str) -> None:
        self._s.execute(
            """INSERT INTO app_user (user_id, email, full_name, password_hash, role,
                                     created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (user_id, email, full_name, password_hash, role, created_at, updated_at))

    def link_apartment_admin(self, apartment_id: str, user_id: str) -> None:
        self._s.execute("UPDATE apartment SET admin_user_id = %s WHERE apartment_id = %s",
                        (user_id, apartment_id))

    def apartments_without_admin(self) -> list[str]:
        rows = self._s.fetch_all(
            "SELECT apartment_id FROM apartment WHERE admin_user_id IS NULL ORDER BY apartment_id")
        return [row["apartment_id"] for row in rows]

    def household_admin_id(self) -> str | None:
        """The earliest household admin, the one who signed up first."""
        row = self._s.fetch_one(
            """SELECT user_id FROM app_user WHERE role = 'household_admin' AND is_active
               ORDER BY created_at, user_id LIMIT 1""")
        return row["user_id"] if row else None

    def insert_session(self, *, session_id: str, user_id: str, created_at: str, expires_at: str,
                       ip_address: str | None, user_agent: str | None) -> None:
        self._s.execute(
            """INSERT INTO user_session (session_id, user_id, created_at, expires_at,
                                         ip_address, user_agent)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (session_id, user_id, created_at, expires_at, ip_address, user_agent))

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """The session with the user it belongs to, expired or not."""
        return self._s.fetch_one(
            """SELECT s.session_id, s.expires_at, u.user_id, u.email, u.full_name, u.role,
                      u.is_active
               FROM user_session s JOIN app_user u ON u.user_id = s.user_id
               WHERE s.session_id = %s""",
            (session_id,))

    def extend_session(self, session_id: str, expires_at: str) -> None:
        self._s.execute("UPDATE user_session SET expires_at = %s WHERE session_id = %s",
                        (expires_at, session_id))

    def delete_session(self, session_id: str) -> None:
        self._s.execute("DELETE FROM user_session WHERE session_id = %s", (session_id,))

    def cleanup_expired_sessions(self, now_ts: str) -> int:
        # Compared as instants: the stored text may carry different UTC offsets.
        return self._s.execute(
            "DELETE FROM user_session WHERE expires_at::timestamptz <= %s::timestamptz",
            (now_ts,))
