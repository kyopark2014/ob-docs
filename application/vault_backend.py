"""Vault storage backend: S3 Files mount, optional S3 API sync, or local disk.

ECS mounts vault/ at /mnt/vault (project S3 bucket).
Locally: data/vault/ is the working copy. Opt-in S3 sync with VAULT_S3_ENABLE=1.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from application import utils

logger = logging.getLogger("vault_backend")

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOCAL = _ROOT / "data" / "vault"
_DEFAULT_MOUNT = Path("/mnt/vault")
S3_PREFIX = "vault/"

_sync_lock = threading.Lock()
_last_sync_at: float = 0.0
_SYNC_INTERVAL_SECONDS = 60.0


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def mount_dir() -> Path:
    raw = (os.environ.get("VAULT_MOUNT") or "").strip()
    if raw:
        return Path(raw)
    cfg = utils.load_config()
    cfg_path = (cfg.get("s3_files_vault_mount_path") or "").strip()
    return Path(cfg_path) if cfg_path else _DEFAULT_MOUNT


def local_dir() -> Path:
    raw = (os.environ.get("VAULT_DIR") or "").strip()
    return Path(raw) if raw else _DEFAULT_LOCAL


def mount_available() -> bool:
    """True only for a real S3 Files mount (or VAULT_USE_MOUNT=1).

    Dockerfile may create an empty /mnt/vault directory; that alone must not
    switch the backend into mount mode or the seeded local vault is skipped.
    """
    if _env_flag("VAULT_USE_MOUNT"):
        path = mount_dir()
        return path.is_dir() and os.access(path, os.W_OK)
    path = mount_dir()
    if not path.is_dir() or not os.access(path, os.W_OK):
        return False
    try:
        return os.path.ismount(str(path))
    except OSError:
        return False


def s3_bucket_and_region() -> tuple[Optional[str], str]:
    cfg = utils.load_config()
    bucket = (
        (os.environ.get("VAULT_S3_BUCKET") or "").strip()
        or (cfg.get("s3_bucket") or "").strip()
        or None
    )
    region = (
        (cfg.get("region") or os.environ.get("AWS_REGION") or "us-west-2").strip()
    )
    return bucket, region


def s3_prefix() -> str:
    cfg = utils.load_config()
    prefix = (cfg.get("s3_files_vault_prefix") or S3_PREFIX).strip() or S3_PREFIX
    return prefix if prefix.endswith("/") else prefix + "/"


def s3_available() -> bool:
    if mount_available():
        return False
    bucket, _ = s3_bucket_and_region()
    if not bucket:
        return False
    if _env_flag("VAULT_S3_DISABLE"):
        return False
    return _env_flag("VAULT_S3_ENABLE")


def backend_mode() -> str:
    if mount_available():
        return "mount"
    if s3_available():
        return "s3"
    return "local"


def vault_root() -> Path:
    """Active vault root for file I/O."""
    if mount_available():
        root = mount_dir()
    else:
        root = local_dir()
    root.mkdir(parents=True, exist_ok=True)
    settings = root / ".vault"
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "cache").mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resolve_vault_path(rel: str) -> Path:
    """Resolve a vault-relative path; reject traversal outside vault."""
    root = vault_root()
    cleaned = (rel or "").replace("\\", "/").lstrip("/")
    if ".." in cleaned.split("/"):
        raise ValueError("Path traversal is not allowed")
    target = (root / cleaned).resolve()
    if root != target and root not in target.parents:
        raise ValueError("Path escapes vault root")
    return target


def find_vault_file(rel: str) -> Optional[Path]:
    """Resolve an existing file; fall back to unique basename match (Obsidian-style)."""
    try:
        target = resolve_vault_path(rel)
    except ValueError:
        return None
    if target.is_file():
        return target
    name = Path((rel or "").replace("\\", "/")).name
    if not name or name in {".", ".."}:
        return None
    root = vault_root()
    matches: list[Path] = []
    for path in root.rglob(name):
        if not path.is_file():
            continue
        if ".vault" in path.parts:
            continue
        matches.append(path)
    if not matches:
        return None
    matches.sort(key=lambda p: (len(p.relative_to(root).parts), str(p).lower()))
    return matches[0]


def settings_dir() -> Path:
    return vault_root() / ".vault"


def cache_dir() -> Path:
    path = settings_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _s3_client(region: str):
    return boto3.client("s3", region_name=region)


def sync_from_s3(*, force: bool = False) -> dict:
    """Download vault/ objects into local working copy (s3 mode only).

    Delegates to vault_sync: never pulls while pending local→S3 ops remain.
    Prefer incremental unless ``force=True``.
    """
    global _last_sync_at
    from application import vault_sync

    # Always try to finish outbound ops first so a pull cannot clobber them.
    flush = vault_sync.flush_pending_to_s3()
    if not flush.get("ok") and flush.get("remaining"):
        return {
            "ok": False,
            "reason": "pending_uploads",
            "flush": flush,
            "skipped": True,
        }
    if not force:
        now = time.time()
        with _sync_lock:
            if (now - _last_sync_at) < _SYNC_INTERVAL_SECONDS and _last_sync_at > 0:
                return {
                    "ok": True,
                    "skipped": True,
                    "flush": flush,
                    "last_sync_at": _last_sync_at,
                }
    return vault_sync.sync_from_s3_incremental(force=force)


def sync_to_s3(rel_path: Optional[str] = None) -> dict:
    """Queue local vault file(s) for S3 upload; flush runs in the background.

    Callers (mkdir/write/upload) return as soon as the local write + pending
    queue entry exist so the UI can refresh without waiting on S3.
    """
    from application import vault_sync

    if backend_mode() != "s3":
        return {"ok": False, "reason": f"backend={backend_mode()}"}
    if rel_path:
        vault_sync.enqueue_put(rel_path)
    else:
        vault_sync.enqueue_put_tree("")
    return vault_sync.schedule_flush_pending()


def ensure_seed_vault() -> None:
    """Copy bundled sample vault into empty local vault."""
    root = vault_root()
    has_md = any(root.rglob("*.md"))
    if has_md:
        return
    seed = _DEFAULT_LOCAL
    if seed.resolve() == root.resolve():
        return
    if not seed.is_dir():
        return
    for src in seed.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(seed)
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copy2(src, dest)
    logger.info("Seeded vault at %s from %s", root, seed)
