"""Shared helpers for ob-docs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_APP_DIR = Path(__file__).resolve().parent
_ROOT_DIR = _APP_DIR.parent
_CONFIG_PATHS = (
    _ROOT_DIR / "config.json",
    _APP_DIR / "config.json",
)

_config_cache: dict[str, Any] | None = None

# Reserved top-level vault segments (not usable as user folders).
RESERVED_VAULT_SEGMENTS = frozenset({"_public", "_legacy"})


def sanitize_user_path_segment(user_id: str | None) -> str | None:
    """Return a safe single path segment for per-user vault folders, or None.

    Mirrors agentic-work: collapse separators so user_id cannot escape the
    intended prefix (e.g. email ``a@b.com`` → ``a@b.com``).
    """
    if not user_id:
        return None
    segment = (
        str(user_id)
        .strip()
        .replace("/", "_")
        .replace("\\", "_")
        .replace("..", "_")
    )
    if not segment or segment in {".", ".."}:
        return None
    if segment in RESERVED_VAULT_SEGMENTS or segment.startswith("_"):
        # Leading underscore is reserved for system folders (_public, …).
        segment = "u_" + segment.lstrip("_")
    return segment or None


def load_config() -> dict[str, Any]:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    env_json = (os.environ.get("APP_CONFIG_JSON") or "").strip()
    if env_json:
        try:
            data = json.loads(env_json)
            if isinstance(data, dict):
                _config_cache = data
                return _config_cache
        except json.JSONDecodeError:
            pass
    for path in _CONFIG_PATHS:
        if path.is_file():
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _config_cache = data
                return _config_cache
    _config_cache = {}
    return _config_cache


def project_name() -> str:
    cfg = load_config()
    name = (
        (cfg.get("projectName") or "").strip()
        or (os.environ.get("PROJECT_NAME") or "").strip()
    )
    return name or "ob-docs"


def sharing_url() -> str:
    """Public CloudFront (or custom domain) base URL for share links."""
    cfg = load_config()
    env = (os.environ.get("SHARING_URL") or os.environ.get("OB_DOCS_URL") or "").strip()
    return (env or cfg.get("sharing_url") or "").strip().rstrip("/")


def is_hybrid_graph_search_enabled() -> bool:
    """Notes graph Ask uses lexical search only (no embeddings by default)."""
    cfg = load_config()
    raw = str(cfg.get("hybrid_graph_search") or "").strip().lower()
    return raw in {"enable", "enabled", "1", "true", "on", "yes"}
