"""Public share links for vault notes (token → path, no cookie required)."""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from application import vault_backend

_lock = threading.Lock()
_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_-]{16,64}$")


def _shares_path() -> Path:
    path = vault_backend.settings_dir() / "shares.json"
    return path


def _s3_shares_location() -> Optional[tuple[str, str, str]]:
    """Return (bucket, region, key) when vault S3 bucket is configured."""
    bucket, region = vault_backend.s3_bucket_and_region()
    if not bucket:
        return None
    key = vault_backend.s3_prefix() + ".vault/shares.json"
    return bucket, region, key


def _publish_shares_to_s3(path: Path) -> None:
    loc = _s3_shares_location()
    if not loc:
        return
    bucket, region, key = loc
    import boto3

    client = boto3.client("s3", region_name=region)
    client.upload_file(str(path), bucket, key)


def pull_shares_registry() -> bool:
    """Refresh local shares.json from S3 so deletes propagate to CloudFront/ECS."""
    loc = _s3_shares_location()
    if not loc:
        return False
    bucket, region, key = loc
    try:
        import boto3
        from botocore.exceptions import ClientError

        client = boto3.client("s3", region_name=region)
        obj = client.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        path = _shares_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return True
    except Exception as e:
        # NoSuchKey / network: keep local copy
        err = getattr(e, "response", None)
        code = ""
        if isinstance(err, dict):
            code = str((err.get("Error") or {}).get("Code") or "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        return False


def _load() -> dict[str, Any]:
    path = _shares_path()
    if not path.is_file():
        return {"shares": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"shares": {}}
    if not isinstance(data, dict):
        return {"shares": {}}
    shares = data.get("shares")
    if not isinstance(shares, dict):
        data["shares"] = {}
    return data


def _save(data: dict[str, Any]) -> None:
    path = _shares_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    # Publish registry to S3 whenever a bucket is configured so CloudFront/ECS
    # can revoke links immediately after Shared List delete.
    try:
        _publish_shares_to_s3(path)
    except Exception:
        try:
            if vault_backend.backend_mode() == "s3":
                from application import vault_sync

                vault_sync.enqueue_put(".vault/shares.json")
                vault_sync.flush_pending_to_s3()
        except Exception:
            pass


def is_valid_token(token: str) -> bool:
    return bool(_TOKEN_RE.match(token or ""))


def find_share_by_path(path: str) -> Optional[dict[str, Any]]:
    cleaned = (path or "").replace("\\", "/").lstrip("/")
    with _lock:
        data = _load()
        for token, entry in (data.get("shares") or {}).items():
            if isinstance(entry, dict) and entry.get("path") == cleaned:
                return {"token": token, **entry}
    return None


def get_share(token: str, *, refresh: bool = True) -> Optional[dict[str, Any]]:
    if not is_valid_token(token):
        return None
    with _lock:
        if refresh:
            pull_shares_registry()
        data = _load()
        entry = (data.get("shares") or {}).get(token)
        if not isinstance(entry, dict):
            return None
        return {"token": token, **entry}


def create_or_get_share(path: str) -> dict[str, Any]:
    """Create a durable share token for a markdown note (reuse if exists)."""
    cleaned = (path or "").replace("\\", "/").lstrip("/")
    if not cleaned.lower().endswith(".md"):
        raise ValueError("Only markdown notes can be shared")
    target = vault_backend.resolve_vault_path(cleaned)
    if not target.is_file():
        raise FileNotFoundError("Note not found")

    title = target.stem
    # Ensure note bytes are on S3 before publishing the share registry
    # (ECS mount may lag behind S3 API uploads from local).
    try:
        publish_vault_file_to_s3(cleaned)
    except Exception:
        pass

    existing = find_share_by_path(cleaned)
    if existing:
        return existing

    token = secrets.token_urlsafe(18)
    entry = {
        "path": cleaned,
        "title": title,
        "created_at": time.time(),
    }
    with _lock:
        data = _load()
        # race: another create may have landed
        for tok, ent in (data.get("shares") or {}).items():
            if isinstance(ent, dict) and ent.get("path") == cleaned:
                return {"token": tok, **ent}
        data.setdefault("shares", {})[token] = entry
        _save(data)
    return {"token": token, **entry}


def list_shares() -> list[dict[str, Any]]:
    """Return share entries newest-first, dropping broken file refs."""
    with _lock:
        pull_shares_registry()
        data = _load()
        items: list[dict[str, Any]] = []
        stale: list[str] = []
        for token, entry in (data.get("shares") or {}).items():
            if not isinstance(entry, dict):
                stale.append(token)
                continue
            path = (entry.get("path") or "").replace("\\", "/").lstrip("/")
            if not path:
                stale.append(token)
                continue
            # Keep share if note exists locally OR on S3 (mount can lag).
            if not vault_object_exists(path):
                stale.append(token)
                continue
            title = entry.get("title") or Path(path).stem
            items.append(
                {
                    "token": token,
                    "path": path,
                    "title": title,
                    "created_at": float(entry.get("created_at") or 0),
                    "url_path": public_share_path(token),
                    "url": public_share_url(token),
                }
            )
        if stale:
            for tok in stale:
                (data.get("shares") or {}).pop(tok, None)
            _save(data)
    items.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return items


def delete_share(token: str) -> bool:
    if not is_valid_token(token):
        return False
    with _lock:
        pull_shares_registry()
        data = _load()
        shares = data.get("shares") or {}
        if token not in shares:
            return False
        shares.pop(token, None)
        data["shares"] = shares
        _save(data)
    return True


def rewrite_share_paths(from_path: str, to_path: str) -> int:
    """Update share records after rename/move. Returns number of updated entries."""
    cleaned_from = (from_path or "").replace("\\", "/").lstrip("/")
    cleaned_to = (to_path or "").replace("\\", "/").lstrip("/")
    if not cleaned_from or cleaned_from == cleaned_to:
        return 0
    updated = 0
    with _lock:
        # Align with CloudFront/ECS registry before rewriting.
        pull_shares_registry()
        data = _load()
        shares = data.get("shares") or {}
        for entry in shares.values():
            if not isinstance(entry, dict):
                continue
            path = (entry.get("path") or "").replace("\\", "/").lstrip("/")
            if path == cleaned_from:
                entry["path"] = cleaned_to
                entry["title"] = Path(cleaned_to).stem
                updated += 1
            elif path.startswith(cleaned_from + "/"):
                entry["path"] = cleaned_to + path[len(cleaned_from) :]
                entry["title"] = Path(entry["path"]).stem
                updated += 1
        if updated:
            _save(data)
    return updated


def remove_shares_for_path(deleted: str) -> int:
    """Drop share entries for a deleted note/folder and publish shares.json to S3."""
    cleaned = (deleted or "").replace("\\", "/").lstrip("/")
    if not cleaned:
        return 0
    removed = 0
    with _lock:
        # Pull first so we revoke shares that only exist on the S3 registry.
        pull_shares_registry()
        data = _load()
        shares = data.get("shares") or {}
        drop = [
            tok
            for tok, entry in shares.items()
            if isinstance(entry, dict)
            and (
                (entry.get("path") or "").replace("\\", "/").lstrip("/") == cleaned
                or str(entry.get("path") or "")
                .replace("\\", "/")
                .lstrip("/")
                .startswith(cleaned + "/")
            )
        ]
        for tok in drop:
            shares.pop(tok, None)
            removed += 1
        if removed:
            data["shares"] = shares
            _save(data)
    return removed


def revoke_share_if_missing(token: str, note_path: str) -> None:
    """If shared note content is gone, drop the token from the registry."""
    if vault_object_exists(note_path):
        return
    delete_share(token)


def public_share_path(token: str) -> str:
    return f"/s/{quote(token, safe='')}"


def public_share_url(token: str) -> str:
    """Absolute public URL (CloudFront sharing_url when configured)."""
    from application import utils

    path = public_share_path(token)
    base = utils.sharing_url()
    if base:
        return f"{base.rstrip('/')}{path}"
    return path


def _s3_object_key(rel_path: str) -> Optional[tuple[str, str, str]]:
    cleaned = (rel_path or "").replace("\\", "/").lstrip("/")
    if not cleaned or ".." in cleaned.split("/"):
        return None
    bucket, region = vault_backend.s3_bucket_and_region()
    if not bucket:
        return None
    return bucket, region, vault_backend.s3_prefix() + cleaned


def vault_object_exists(rel_path: str) -> bool:
    """True if file exists on local vault or as an S3 object."""
    cleaned = (rel_path or "").replace("\\", "/").lstrip("/")
    if not cleaned:
        return False
    try:
        if vault_backend.resolve_vault_path(cleaned).is_file():
            return True
    except ValueError:
        pass
    loc = _s3_object_key(cleaned)
    if not loc:
        return False
    bucket, region, key = loc
    try:
        import boto3

        boto3.client("s3", region_name=region).head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def publish_vault_file_to_s3(rel_path: str) -> bool:
    """Upload a vault-relative file to S3 so CloudFront/ECS can serve it."""
    cleaned = (rel_path or "").replace("\\", "/").lstrip("/")
    try:
        target = vault_backend.resolve_vault_path(cleaned)
    except ValueError:
        return False
    if not target.is_file():
        return False
    loc = _s3_object_key(cleaned)
    if not loc:
        return False
    bucket, region, key = loc
    import boto3

    boto3.client("s3", region_name=region).upload_file(str(target), bucket, key)
    return True


def delete_vault_from_s3(rel_path: str) -> bool:
    """Delete a vault-relative object from S3 when a bucket is configured."""
    cleaned = (rel_path or "").replace("\\", "/").lstrip("/")
    loc = _s3_object_key(cleaned)
    if not loc:
        return False
    bucket, region, key = loc
    try:
        import boto3

        boto3.client("s3", region_name=region).delete_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False


def delete_vault_tree_from_s3(rel_path: str) -> int:
    """Delete a file or all objects under a vault-relative prefix from S3."""
    cleaned = (rel_path or "").replace("\\", "/").lstrip("/")
    if not cleaned:
        return 0
    bucket, region = vault_backend.s3_bucket_and_region()
    if not bucket:
        return 0
    prefix = vault_backend.s3_prefix()
    import boto3

    client = boto3.client("s3", region_name=region)
    # Exact key (file) + children prefix (folder)
    keys = {prefix + cleaned, prefix + cleaned + "/"}
    # List children for folders
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix + cleaned + "/"):
            for obj in page.get("Contents") or []:
                keys.add(obj["Key"])
    except Exception:
        pass
    deleted = 0
    for key in keys:
        try:
            client.delete_object(Bucket=bucket, Key=key)
            deleted += 1
        except Exception:
            continue
    return deleted


def read_vault_text(rel_path: str) -> Optional[str]:
    """Read note text: local vault first, then S3 (mount lag / API-upload gap)."""
    cleaned = (rel_path or "").replace("\\", "/").lstrip("/")
    try:
        target = vault_backend.resolve_vault_path(cleaned)
        if target.is_file():
            return target.read_text(encoding="utf-8", errors="replace")
    except ValueError:
        pass
    loc = _s3_object_key(cleaned)
    if not loc:
        return None
    bucket, region, key = loc
    try:
        import boto3

        obj = boto3.client("s3", region_name=region).get_object(Bucket=bucket, Key=key)
        return obj["Body"].read().decode("utf-8", errors="replace")
    except Exception:
        return None


def read_vault_bytes(rel_path: str) -> Optional[bytes]:
    """Read binary vault object: local first, then S3."""
    cleaned = (rel_path or "").replace("\\", "/").lstrip("/")
    try:
        target = vault_backend.resolve_vault_path(cleaned)
        if target.is_file():
            return target.read_bytes()
    except ValueError:
        pass
    loc = _s3_object_key(cleaned)
    if not loc:
        return None
    bucket, region, key = loc
    try:
        import boto3

        obj = boto3.client("s3", region_name=region).get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()
    except Exception:
        return None


def rewrite_md_assets_for_share(text: str, token: str) -> str:
    """Point relative markdown images/links at the public share asset endpoint."""

    def repl(match: re.Match[str]) -> str:
        alt = match.group(1)
        dest = match.group(2).strip()
        if dest.startswith("<") and dest.endswith(">"):
            inner = dest[1:-1].strip()
            wrapped = True
        else:
            inner = dest
            wrapped = False
        if (
            not inner
            or inner.startswith("http://")
            or inner.startswith("https://")
            or inner.startswith("data:")
            or inner.startswith("/")
            or inner.startswith("#")
        ):
            return match.group(0)
        url = f"/s/{quote(token, safe='')}/raw?path={quote(inner, safe='')}"
        if wrapped:
            return f"![{alt}](<{url}>)"
        return f"![{alt}]({url})"

    return re.sub(r"!\[([^\]]*)\]\(([^)\n]+)\)", repl, text or "")
