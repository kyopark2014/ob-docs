"""Session auth — Google sign-in + shared agent_user_id cookie with agentic-work.

Also accepts AgentCore ``Authorization: VaultAgent …`` credentials signed with
the shared ``vault-agent-token`` (runtime is denied session-signing-key).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from application import session_cookie, utils, vault_agent_auth

logger = logging.getLogger("routes_auth")

router = APIRouter(prefix="/vault/api", tags=["session"])

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


class SessionRequest(BaseModel):
    credential: str | None = Field(
        default=None,
        description="Google ID token (GIS credential)",
    )
    access_token: str | None = Field(
        default=None,
        description="Google OAuth access token",
    )
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


class PublicConfigResponse(BaseModel):
    google_client_id: str
    local_auth_bypass: bool
    agentic_work_url: str
    project_name: str = "ob-docs"


def _google_client_id() -> str:
    cfg = utils.load_config()
    client_id = (cfg.get("google_client_id") or "").strip()
    if not client_id:
        raise HTTPException(status_code=500, detail="google_client_id is not configured")
    return client_id


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


def local_auth_bypass_enabled(request: Request) -> bool:
    """True when ALLOW_LOCAL_AUTH_BYPASS is set (local run scripts)."""
    return _env_bypass_flag() and is_loopback_request(request)


def verify_google_token(token: str, client_id: str) -> dict:
    """Verify Google ID Token via tokeninfo (no extra dependency)."""
    url = f"{TOKENINFO_URL}?id_token={token}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            idinfo = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ValueError(f"Token verification failed ({e.code}): {body}") from e
    except Exception as e:
        raise ValueError(f"Token verification request failed: {e}") from e

    if idinfo.get("aud") != client_id:
        raise ValueError(f"Invalid audience: {idinfo.get('aud')}")
    if idinfo.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError(f"Invalid issuer: {idinfo.get('iss')}")
    email = (idinfo.get("email") or "").strip()
    if not email:
        raise ValueError("Token does not contain email")
    if idinfo.get("email_verified") in ("false", False):
        raise ValueError("Email is not verified")
    return idinfo


def verify_google_access_token(token: str, client_id: str) -> dict:
    """Verify Google OAuth access token and load profile (email/name/picture)."""
    info_url = f"{TOKENINFO_URL}?access_token={urllib.parse.quote(token)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(info_url), timeout=5) as resp:
            tokeninfo = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ValueError(f"Access token verification failed ({e.code}): {body}") from e
    except Exception as e:
        raise ValueError(f"Access token verification request failed: {e}") from e

    audience = (tokeninfo.get("aud") or tokeninfo.get("azp") or "").strip()
    if audience != client_id:
        raise ValueError(f"Invalid access token audience: {audience}")

    userinfo_req = urllib.request.Request(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(userinfo_req, timeout=5) as resp:
            profile = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ValueError(f"Userinfo request failed ({e.code}): {body}") from e
    except Exception as e:
        raise ValueError(f"Userinfo request failed: {e}") from e

    email = (profile.get("email") or tokeninfo.get("email") or "").strip()
    if not email:
        raise ValueError("Access token profile does not contain email")
    verified = profile.get("email_verified", tokeninfo.get("email_verified"))
    if verified in ("false", False):
        raise ValueError("Email is not verified")

    return {
        "email": email,
        "name": profile.get("name"),
        "picture": profile.get("picture"),
        "sub": profile.get("sub") or tokeninfo.get("sub"),
        "email_verified": True,
    }


def _cookie_secure(request: Request) -> bool:
    proto = (
        request.headers.get("cloudfront-forwarded-proto")
        or request.headers.get("x-forwarded-proto")
        or request.url.scheme
        or ""
    ).lower()
    if proto == "https":
        return True
    host = (request.headers.get("host") or request.url.hostname or "").split(":")[0].lower()
    if host.endswith(".cloudfront.net"):
        return True
    try:
        sharing = (utils.load_config().get("sharing_url") or "").strip()
        parsed = urlparse(sharing)
        if parsed.scheme == "https" and (parsed.hostname or "").lower() == host:
            return True
    except Exception:
        pass
    return False


def _set_user_cookie(response: Response, request: Request, user_id: str) -> None:
    token = session_cookie.sign_session(user_id)
    response.set_cookie(
        key=session_cookie.COOKIE_NAME,
        value=token,
        max_age=session_cookie.session_max_age_seconds(),
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
    )


def _token_from_request(request: Request) -> str | None:
    """Prefer cookie; also accept Bearer / X-Vault-Session for agent scripts."""
    raw = request.cookies.get(session_cookie.COOKIE_NAME)
    if raw:
        return raw
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    header = (request.headers.get("x-vault-session") or "").strip()
    return header or None


def _resolve_user_id(request: Request) -> str | None:
    user_id = session_cookie.verify_session(_token_from_request(request))
    if user_id:
        return user_id
    return vault_agent_auth.verify_vault_agent_authorization(
        request.headers.get("authorization")
    )


def require_user_id(request: Request) -> str:
    user_id = _resolve_user_id(request)
    if user_id:
        return user_id
    if local_auth_bypass_enabled(request):
        return "local-dev"
    raise HTTPException(
        status_code=401,
        detail={
            "error": "unauthorized",
            "login_url": utils.agentic_work_url(),
            "message": "Sign in with Google to continue",
        },
    )


@router.get("/config", response_model=PublicConfigResponse)
def get_public_config(request: Request) -> PublicConfigResponse:
    cfg = utils.load_config()
    return PublicConfigResponse(
        google_client_id=(cfg.get("google_client_id") or "").strip(),
        local_auth_bypass=local_auth_bypass_enabled(request),
        agentic_work_url=utils.agentic_work_url(),
        project_name=(cfg.get("projectName") or "ob-docs").strip() or "ob-docs",
    )


@router.get("/session", response_model=SessionResponse)
def get_session(request: Request) -> SessionResponse:
    user_id = _resolve_user_id(request)
    if user_id:
        return SessionResponse(
            user_id=user_id,
            agentic_work_url=utils.agentic_work_url(),
            authenticated=True,
        )
    if local_auth_bypass_enabled(request):
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


@router.post("/session", response_model=SessionResponse)
def set_session(
    request: Request, body: SessionRequest, response: Response
) -> SessionResponse:
    """Create session via Google token, or local bypass user_id."""
    credential = (body.credential or "").strip()
    access_token = (body.access_token or "").strip()
    local_user_id = (body.user_id or "").strip()

    if credential or access_token:
        try:
            if credential:
                idinfo = verify_google_token(credential, _google_client_id())
            else:
                idinfo = verify_google_access_token(access_token, _google_client_id())
        except ValueError as e:
            logger.warning("Google login rejected: %s", e)
            raise HTTPException(status_code=401, detail="Invalid Google credential") from e

        user_id = idinfo["email"].strip()
        _set_user_cookie(response, request, user_id)
        return SessionResponse(
            user_id=user_id,
            agentic_work_url=utils.agentic_work_url(),
            authenticated=True,
        )

    if not local_auth_bypass_enabled(request):
        raise HTTPException(
            status_code=403,
            detail="Provide a Google credential, or enable local auth bypass",
        )
    user_id = local_user_id or "local-dev"
    _set_user_cookie(response, request, user_id)
    return SessionResponse(
        user_id=user_id,
        agentic_work_url=utils.agentic_work_url(),
        authenticated=True,
    )


@router.delete("/session")
def clear_session(request: Request, response: Response) -> dict:
    response.delete_cookie(
        key=session_cookie.COOKIE_NAME,
        path="/",
        samesite="lax",
        secure=_cookie_secure(request),
    )
    return {"ok": True}
