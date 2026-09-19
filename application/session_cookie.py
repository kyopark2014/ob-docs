"""HMAC-signed session cookies for ob-docs.

Cookie: agent_user_id = v1.<payload_b64>.<sig_b64>
Signing key resolution: SESSION_SIGNING_KEY → Secrets Manager ({project}/session-signing-key) → local file.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("session_cookie")

COOKIE_NAME = "agent_user_id"
COOKIE_VERSION = "v1"
DEFAULT_MAX_AGE_SECONDS = 60 * 60 * 24 * 30
_ENV_KEY = "SESSION_SIGNING_KEY"
_LOCAL_KEY_FILE = Path(__file__).resolve().parent / "data" / ".session_signing_key"

_key_lock = threading.Lock()
_cached_key: Optional[bytes] = None


def session_max_age_seconds() -> int:
    raw = (os.environ.get("SESSION_MAX_AGE_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_MAX_AGE_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_AGE_SECONDS
    return value if value > 0 else DEFAULT_MAX_AGE_SECONDS


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _project_name() -> str:
    try:
        from application import utils

        return utils.project_name()
    except Exception:
        return (
            os.environ.get("PROJECT_NAME")
            or os.environ.get("SHARED_PROJECT_NAME")
            or "ob-docs"
        ).strip() or "ob-docs"


def _secret_name() -> str:
    return f"{_project_name()}/session-signing-key"


def _load_key_from_secrets_manager() -> Optional[bytes]:
    try:
        import boto3

        region = (
            os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-west-2"
        )
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=_secret_name())
        secret = (response.get("SecretString") or "").strip()
        if secret:
            return secret.encode("utf-8")
    except Exception as e:
        logger.debug("Session signing key not loaded from Secrets Manager: %s", e)
    return None


def _load_or_create_local_key() -> bytes:
    _LOCAL_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if _LOCAL_KEY_FILE.exists():
        existing = _LOCAL_KEY_FILE.read_text(encoding="utf-8").strip()
        if existing:
            return existing.encode("utf-8")
    value = secrets.token_urlsafe(32)
    _LOCAL_KEY_FILE.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(_LOCAL_KEY_FILE, 0o600)
    except OSError:
        pass
    logger.info("Created local session signing key at %s", _LOCAL_KEY_FILE)
    return value.encode("utf-8")


def get_signing_key() -> bytes:
    global _cached_key
    if _cached_key is not None:
        return _cached_key
    with _key_lock:
        if _cached_key is not None:
            return _cached_key
        env_key = (os.environ.get(_ENV_KEY) or "").strip()
        if env_key:
            _cached_key = env_key.encode("utf-8")
            return _cached_key
        sm_key = _load_key_from_secrets_manager()
        if sm_key:
            _cached_key = sm_key
            return _cached_key
        _cached_key = _load_or_create_local_key()
        return _cached_key


def sign_session(
    user_id: str,
    *,
    max_age_seconds: Optional[int] = None,
    issued_at: Optional[int] = None,
) -> str:
    """Return an HMAC-signed cookie value.

    Payload: ``{"uid": "...", "exp": <unix>}``.
    ``issued_at`` is accepted for callers that still pass it; expiry is
    ``now + max_age`` regardless.
    """
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required")
    age = session_max_age_seconds() if max_age_seconds is None else int(max_age_seconds)
    now = int(time.time()) if issued_at is None else int(issued_at)
    exp = now + max(age, 1)
    payload = json.dumps({"uid": uid, "exp": exp}, separators=(",", ":"), ensure_ascii=False)
    payload_b64 = _b64encode(payload.encode("utf-8"))
    sig = hmac.new(get_signing_key(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{COOKIE_VERSION}.{payload_b64}.{_b64encode(sig)}"


def verify_session(cookie_value: str | None) -> Optional[str]:
    """Return user_id if the signed cookie is valid; otherwise None.

    Accepts tokens with ``uid``+``exp`` and legacy tokens with ``uid``+``iat``.
    """
    raw = (cookie_value or "").strip()
    if not raw:
        return None
    parts = raw.split(".")
    if len(parts) != 3 or parts[0] != COOKIE_VERSION:
        return None
    _, payload_b64, sig_b64 = parts
    try:
        expected = hmac.new(
            get_signing_key(), payload_b64.encode("ascii"), hashlib.sha256
        ).digest()
        actual = _b64decode(sig_b64)
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
        user_id = payload.get("uid")
        if not isinstance(user_id, str) or not user_id.strip():
            return None
        now = int(time.time())
        if "exp" in payload:
            exp = int(payload.get("exp") or 0)
            if exp < now:
                return None
        else:
            # Legacy iat-based tokens
            iat = int(payload.get("iat") or 0)
            if iat and (now - iat) > session_max_age_seconds():
                return None
        return user_id.strip()
    except Exception:
        return None
