"""Session auth — shares agent_user_id cookie with agentic-work."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from application import session_cookie, utils

router = APIRouter(prefix="/vault/api/session", tags=["session"])


class SessionRequest(BaseModel):
    user_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Local-only user id when auth bypass is enabled",
    )


class SessionResponse(BaseModel):
    user_id: str
    agentic_work_url: str
    authenticated: bool = True


def _env_bypass_flag() -> bool:
    return os.environ.get("ALLOW_LOCAL_AUTH_BYPASS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_loopback_request(request: Request) -> bool:
    host = (request.headers.get("host") or "").split("%")[0]
    hostname = host.split(":")[0].strip().lower().strip("[]")
    return hostname in {"localhost", "127.0.0.1", "::1"}


def require_user_id(request: Request) -> str:
    raw = request.cookies.get(session_cookie.COOKIE_NAME)
    user_id = session_cookie.verify_session(raw)
    if user_id:
        return user_id
    if _env_bypass_flag() and is_loopback_request(request):
        return "local-dev"
    raise HTTPException(
        status_code=401,
        detail={
            "error": "unauthorized",
            "login_url": utils.agentic_work_url(),
            "message": "Sign in via agentic-work, then return to /vault",
        },
    )


@router.get("", response_model=SessionResponse)
def get_session(request: Request) -> SessionResponse:
    raw = request.cookies.get(session_cookie.COOKIE_NAME)
    user_id = session_cookie.verify_session(raw)
    if user_id:
        return SessionResponse(
            user_id=user_id,
            agentic_work_url=utils.agentic_work_url(),
            authenticated=True,
        )
    if _env_bypass_flag() and is_loopback_request(request):
        return SessionResponse(
            user_id="local-dev",
            agentic_work_url=utils.agentic_work_url(),
            authenticated=True,
        )
    raise HTTPException(
        status_code=401,
        detail={
            "error": "unauthorized",
            "login_url": utils.agentic_work_url(),
        },
    )


@router.post("", response_model=SessionResponse)
def create_local_session(
    request: Request, body: SessionRequest, response: Response
) -> SessionResponse:
    """Local bypass only — production users authenticate via agentic-work."""
    if not (_env_bypass_flag() and is_loopback_request(request)):
        raise HTTPException(status_code=403, detail="Local auth bypass disabled")
    user_id = (body.user_id or "local-dev").strip()
    token = session_cookie.sign_session(user_id)
    response.set_cookie(
        key=session_cookie.COOKIE_NAME,
        value=token,
        max_age=session_cookie.session_max_age_seconds(),
        httponly=True,
        samesite="lax",
        path="/",
    )
    return SessionResponse(
        user_id=user_id,
        agentic_work_url=utils.agentic_work_url(),
        authenticated=True,
    )


@router.delete("")
def clear_session(response: Response) -> dict:
    response.delete_cookie(session_cookie.COOKIE_NAME, path="/")
    return {"ok": True}
