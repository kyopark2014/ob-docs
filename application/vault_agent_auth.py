"""Vault agent-token auth (AgentCore → ob-note).

AgentCore runtime is denied access to session-signing-key by design.
Instead it reads ``{project}/vault-agent-token`` and sends:

  Authorization: VaultAgent v1.<payload_b64>.<sig_b64>

Payload is JSON ``{"uid":"<user_id>","exp":<unix>}`` HMAC-SHA256 signed with
the vault agent token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("vault_agent_auth")

SCHEME = "VaultAgent"
VERSION = "v1"
DEFAULT_MAX_AGE_SECONDS = 60 * 60  # 1 hour for agent calls
_ENV_TOKEN = "VAULT_AGENT_TOKEN"

_token_lock = threading.Lock()
_cached_token: Optional[bytes] = None


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
            or "ob-note"
        ).strip() or "ob-note"


def _secret_name() -> str:
    return f"{_project_name()}/vault-agent-token"


def _load_token_from_secrets_manager() -> Optional[bytes]:
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
        logger.debug("vault-agent-token not loaded from Secrets Manager: %s", e)
    return None


def get_vault_agent_token() -> Optional[bytes]:
    """Resolve token: env VAULT_AGENT_TOKEN → Secrets Manager. None if missing."""
    global _cached_token
    if _cached_token is not None:
        return _cached_token
    with _token_lock:
        if _cached_token is not None:
            return _cached_token
        env = (os.environ.get(_ENV_TOKEN) or "").strip()
        if env:
            _cached_token = env.encode("utf-8")
            return _cached_token
        sm = _load_token_from_secrets_manager()
        if sm:
            _cached_token = sm
            return _cached_token
        return None


def reset_vault_agent_token_cache() -> None:
    global _cached_token
    with _token_lock:
        _cached_token = None


def sign_vault_agent(user_id: str, *, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> str:
    """Return ``VaultAgent`` credential value (without scheme prefix)."""
    token = get_vault_agent_token()
    if not token:
        raise RuntimeError(
            f"{_ENV_TOKEN} / Secrets Manager `{_secret_name()}` not available"
        )
    uid = (user_id or "").strip()
    if not uid:
        raise ValueError("user_id is required")
    exp = int(time.time()) + max(int(max_age_seconds), 60)
    payload = json.dumps({"uid": uid, "exp": exp}, separators=(",", ":"), ensure_ascii=False)
    payload_b64 = _b64encode(payload.encode("utf-8"))
    sig = hmac.new(token, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{VERSION}.{payload_b64}.{_b64encode(sig)}"


def verify_vault_agent_authorization(authorization: str | None) -> Optional[str]:
    """Return user_id if Authorization is a valid VaultAgent token."""
    raw = (authorization or "").strip()
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) != 2 or parts[0] != SCHEME:
        return None
    credential = parts[1].strip()
    pieces = credential.split(".")
    if len(pieces) != 3 or pieces[0] != VERSION:
        return None
    _, payload_b64, sig_b64 = pieces
    token = get_vault_agent_token()
    if not token:
        return None
    try:
        expected = hmac.new(token, payload_b64.encode("ascii"), hashlib.sha256).digest()
        provided = _b64decode(sig_b64)
        if not hmac.compare_digest(expected, provided):
            return None
        payload = json.loads(_b64decode(payload_b64).decode("utf-8"))
        uid = (payload.get("uid") or "").strip()
        exp = int(payload.get("exp") or 0)
        if not uid or exp < int(time.time()):
            return None
        return uid
    except Exception:
        return None
