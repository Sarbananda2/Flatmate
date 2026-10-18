"""Signing up the household admin, logging in and out.

The session travels in an HTTP-only cookie the page's scripts cannot read,
sent only to this site (SameSite=Lax), and marked Secure over HTTPS.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from ...errors import AemError
from ...schemas import AuthStatusResponse, AuthUserResponse, LoginRequest, SignupRequest
from ...services import auth
from ..deps import SESSION_COOKIE, CurrentUser, Db, session_token, to_http

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _client(request: Request) -> dict[str, str | None]:
    return {"ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent")}


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    secure = request.app.state.settings.secure_cookies or request.url.scheme == "https"
    response.set_cookie(
        SESSION_COOKIE, token, max_age=int(auth.SESSION_LIFETIME.total_seconds()),
        path="/", httponly=True, samesite="lax", secure=secure)


@router.get("/status", response_model=AuthStatusResponse,
            summary="Whether anyone has signed up, and who is signed in")
def status(request: Request, db: Db) -> dict[str, Any]:
    user = auth.validate_session(db, session_token(request))
    return {"setup_required": auth.get_auth_status(db)["setup_required"],
            "authenticated": user is not None, "user": user}


@router.post("/signup", status_code=201, response_model=AuthUserResponse,
             summary="Create the household admin's account (only before anyone has)")
def signup(payload: SignupRequest, request: Request, response: Response,
           db: Db) -> dict[str, Any]:
    try:
        user, token = auth.register_household_admin(
            db, email=payload.email, password=payload.password,
            full_name=payload.full_name, **_client(request))
    except AemError as exc:
        raise to_http(exc) from exc
    _set_session_cookie(request, response, token)
    return {"user": user}


@router.post("/login", response_model=AuthUserResponse, summary="Log in")
def login(payload: LoginRequest, request: Request, response: Response,
          db: Db) -> dict[str, Any]:
    try:
        user, token = auth.authenticate(db, email=payload.email, password=payload.password,
                                        **_client(request))
    except AemError as exc:
        raise to_http(exc) from exc
    _set_session_cookie(request, response, token)
    return {"user": user}


@router.post("/logout", status_code=204, summary="Log out, ending this session")
def logout(request: Request, db: Db) -> Response:
    auth.logout(db, session_token(request))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="lax")
    return response


@router.get("/me", response_model=AuthUserResponse, summary="The signed-in user")
def me(user: CurrentUser) -> dict[str, Any]:
    return {"user": user}
