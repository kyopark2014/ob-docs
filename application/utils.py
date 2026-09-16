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
    name = (cfg.get("projectName") or "").strip()
    return name or "ob-docs"


def shared_project_name() -> str:
    cfg = load_config()
    name = (cfg.get("sharedProjectName") or "").strip()
    return name or "agentic-work"


def agentic_work_url() -> str:
    cfg = load_config()
    return (
        (cfg.get("agentic_work_url") or cfg.get("sharing_url") or "").strip()
        or "http://localhost:8501"
    )
