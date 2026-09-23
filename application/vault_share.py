"""Public share links for vault notes and folders (token → path, no cookie required).

Per-user registry: ``{user}/.vault/shares.json`` (list/create/delete).
Global index: ``_public/shares_index.json`` (token → user_id + path) so
anonymous ``/s/{token}`` can resolve the owning vault without scanning users.

Share ``type``:
  - ``note`` (default / missing): single markdown file at ``path``
  - ``folder``: folder at ``path``; public index lists direct ``.md`` children only;
    notes open under ``/s/{token}/n/{name}`` (no per-note tokens).
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from application import vault_backend

_lock = threading.Lock()
_TOKEN_RE = re.compile(r"^[a-zA-Z0-9_-]{16,64}$")
_INDEX_NAME = "shares_index.json"


def _shares_path() -> Path:
    return vault_backend.settings_dir() / "shares.json"


def _index_path() -> Path:
    return vault_backend.public_dir() / _INDEX_NAME


def _s3_shares_location() -> Optional[tuple[str, str, str]]:
    """Return (bucket, region, key) for the current user's shares.json."""
    bucket, region = vault_backend.s3_bucket_and_region()
    if not bucket:
        return None
    key = vault_backend.s3_prefix() + ".vault/shares.json"
    return bucket, region, key


def _s3_index_location() -> Optional[tuple[str, str, str]]:
    bucket, region = vault_backend.s3_bucket_and_region()
    if not bucket:
        return None
    key = vault_backend.s3_public_prefix() + _INDEX_NAME
    return bucket, region, key


def _publish_shares_to_s3(path: Path) -> None:
    loc = _s3_shares_location()
    if not loc:
        return
    bucket, region, key = loc
    import boto3

    client = boto3.client("s3", region_name=region)
    client.upload_file(str(path), bucket, key)


def _publish_index_to_s3(path: Path) -> None:
    loc = _s3_index_location()
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
        err = getattr(e, "response", None)
        code = ""
        if isinstance(err, dict):
            code = str((err.get("Error") or {}).get("Code") or "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        return False


def pull_shares_index() -> bool:
    """Refresh global token→user index from S3."""
    loc = _s3_index_location()
    if not loc:
        return False
    bucket, region, key = loc
    try:
        import boto3

        client = boto3.client("s3", region_name=region)
        obj = client.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()
        path = _index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return True
    except Exception:
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


def _load_index() -> dict[str, Any]:
    path = _index_path()
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


def _save_index(data: dict[str, Any]) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    try:
        _publish_index_to_s3(path)
    except Exception:
        pass


def share_entry_type(entry: Optional[dict[str, Any]]) -> str:
    """Return ``note`` or ``folder`` (missing type → note for backward compat)."""
    if not isinstance(entry, dict):
        return "note"
    if entry.get("type") == "folder":
        return "folder"
    return "note"


def _index_upsert(token: str, entry: dict[str, Any]) -> None:
    user_id = entry.get("user_id") or vault_backend.current_user_id()
    if not user_id:
        return
    index = _load_index()
    payload: dict[str, Any] = {
        "user_id": user_id,
        "path": entry.get("path"),
        "title": entry.get("title"),
        "created_at": entry.get("created_at"),
    }
    st = share_entry_type(entry)
    if st == "folder":
        payload["type"] = "folder"
    index.setdefault("shares", {})[token] = payload
    _save_index(index)


def _index_remove(tokens: list[str]) -> None:
    if not tokens:
        return
    index = _load_index()
    shares = index.get("shares") or {}
    changed = False
    for tok in tokens:
        if shares.pop(tok, None) is not None:
            changed = True
    if changed:
        index["shares"] = shares
        _save_index(index)


def _index_update_paths(from_path: str, to_path: str, user_id: Optional[str]) -> None:
    if not user_id:
        return
    index = _load_index()
    shares = index.get("shares") or {}
    updated = 0
    for entry in shares.values():
        if not isinstance(entry, dict):
            continue
        if entry.get("user_id") != user_id:
            continue
        path = (entry.get("path") or "").replace("\\", "/").lstrip("/")
        if path == from_path:
            entry["path"] = to_path
            if share_entry_type(entry) == "folder":
                entry["title"] = Path(to_path).name
            else:
                entry["title"] = Path(to_path).stem
            updated += 1
        elif path.startswith(from_path + "/"):
            entry["path"] = to_path + path[len(from_path) :]
            if share_entry_type(entry) == "folder":
                entry["title"] = Path(entry["path"]).name
            else:
                entry["title"] = Path(entry["path"]).stem
            updated += 1
    if updated:
        _save_index(index)


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
    """Resolve a share token via the global index (public) or user registry."""
    if not is_valid_token(token):
        return None
    with _lock:
        if refresh:
            pull_shares_index()
        index = _load_index()
        entry = (index.get("shares") or {}).get(token)
        if isinstance(entry, dict) and entry.get("user_id") and entry.get("path"):
            return {"token": token, **entry}
        if vault_backend.current_user_id():
            if refresh:
                pull_shares_registry()
            data = _load()
            local = (data.get("shares") or {}).get(token)
            if isinstance(local, dict):
                return {
                    "token": token,
                    "user_id": vault_backend.current_user_id(),
                    **local,
                }
    return None


def create_or_get_share(path: str) -> dict[str, Any]:
    """Create a durable share token for a markdown note or folder (reuse if exists)."""
    cleaned = (path or "").replace("\\", "/").lstrip("/")
    if not cleaned:
        raise ValueError("Path is required")
    if ".." in cleaned.split("/"):
        raise ValueError("Invalid path")

    user_id = vault_backend.current_user_id()
    if not user_id:
        raise ValueError("vault user_id is required to create a share")

    if cleaned.lower().endswith(".md"):
        share_type = "note"
        target = vault_backend.resolve_vault_path(cleaned)
        if not target.is_file():
            raise FileNotFoundError("Note not found")
        title = target.stem
        try:
            publish_vault_file_to_s3(cleaned)
        except Exception:
            pass
    else:
        share_type = "folder"
        if not vault_folder_exists(cleaned):
            raise FileNotFoundError("Folder not found")
        title = Path(cleaned).name

    existing = find_share_by_path(cleaned)
    if existing:
        if "user_id" not in existing:
            existing = {**existing, "user_id": user_id}
        if share_entry_type(existing) != share_type:
            existing = {**existing, "type": share_type, "title": title}
        with _lock:
            pull_shares_index()
            _index_upsert(existing["token"], existing)
        return existing

    token = secrets.token_urlsafe(18)
    entry: dict[str, Any] = {
        "path": cleaned,
        "title": title,
        "created_at": time.time(),
        "user_id": user_id,
        "type": share_type,
    }
    with _lock:
        data = _load()
        for tok, ent in (data.get("shares") or {}).items():
            if isinstance(ent, dict) and ent.get("path") == cleaned:
                out = {"token": tok, "user_id": user_id, **ent}
                if "type" not in out:
                    out["type"] = share_type
                pull_shares_index()
                _index_upsert(tok, out)
                return out
        stored: dict[str, Any] = {
            "path": cleaned,
            "title": title,
            "created_at": entry["created_at"],
            "type": share_type,
        }
        data.setdefault("shares", {})[token] = stored
        _save(data)
        pull_shares_index()
        _index_upsert(token, entry)
    return {"token": token, **entry}


def list_shares() -> list[dict[str, Any]]:
    """Return share entries newest-first, dropping broken file/folder refs."""
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
            st = share_entry_type(entry)
            if st == "folder":
                if not vault_folder_exists(path):
                    stale.append(token)
                    continue
                title = entry.get("title") or Path(path).name
            else:
                if not vault_object_exists(path):
                    stale.append(token)
                    continue
                title = entry.get("title") or Path(path).stem
            items.append(
                {
                    "token": token,
                    "path": path,
                    "title": title,
                    "type": st,
                    "created_at": float(entry.get("created_at") or 0),
                    "url_path": public_share_path(token),
                    "url": public_share_url(token),
                }
            )
        if stale:
            for tok in stale:
                (data.get("shares") or {}).pop(tok, None)
            _save(data)
            pull_shares_index()
            _index_remove(stale)
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
            pull_shares_index()
            _index_remove([token])
            return False
        shares.pop(token, None)
        data["shares"] = shares
        _save(data)
        pull_shares_index()
        _index_remove([token])
    return True


def rewrite_share_paths(from_path: str, to_path: str) -> int:
    """Update share records after rename/move. Returns number of updated entries."""
    cleaned_from = (from_path or "").replace("\\", "/").lstrip("/")
    cleaned_to = (to_path or "").replace("\\", "/").lstrip("/")
    if not cleaned_from or cleaned_from == cleaned_to:
        return 0
    updated = 0
    user_id = vault_backend.current_user_id()
    with _lock:
        pull_shares_registry()
        data = _load()
        shares = data.get("shares") or {}
        for entry in shares.values():
            if not isinstance(entry, dict):
                continue
            path = (entry.get("path") or "").replace("\\", "/").lstrip("/")
            if path == cleaned_from:
                entry["path"] = cleaned_to
                if share_entry_type(entry) == "folder":
                    entry["title"] = Path(cleaned_to).name
                else:
                    entry["title"] = Path(cleaned_to).stem
                updated += 1
            elif path.startswith(cleaned_from + "/"):
                entry["path"] = cleaned_to + path[len(cleaned_from) :]
                if share_entry_type(entry) == "folder":
                    entry["title"] = Path(entry["path"]).name
                else:
                    entry["title"] = Path(entry["path"]).stem
                updated += 1
        if updated:
            _save(data)
            pull_shares_index()
            _index_update_paths(cleaned_from, cleaned_to, user_id)
    return updated


def remove_shares_for_path(deleted: str) -> int:
    """Drop share entries for a deleted note/folder and publish shares.json to S3."""
    cleaned = (deleted or "").replace("\\", "/").lstrip("/")
    if not cleaned:
        return 0
    removed = 0
    with _lock:
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
            pull_shares_index()
            _index_remove(drop)
    return removed


def revoke_share_if_missing(token: str, note_path: str) -> None:
    """If shared note content is gone, drop the token from the registry."""
    if vault_object_exists(note_path):
        return
    delete_share(token)


def public_share_path(token: str) -> str:
    return f"/s/{quote(token, safe='')}"


def public_folder_note_path(token: str, name: str) -> str:
    """Public path for a direct note under a folder share."""
    return f"/s/{quote(token, safe='')}/n/{quote(name, safe='')}"


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


def vault_folder_exists(rel_path: str) -> bool:
    """True if folder exists locally or has at least one object under its S3 prefix."""
    cleaned = (rel_path or "").replace("\\", "/").lstrip("/")
    if not cleaned or ".." in cleaned.split("/"):
        return False
    try:
        target = vault_backend.resolve_vault_path(cleaned)
        if target.is_dir():
            return True
    except ValueError:
        pass
    bucket, region = vault_backend.s3_bucket_and_region()
    if not bucket:
        return False
    prefix = vault_backend.s3_prefix() + cleaned + "/"
    try:
        import boto3

        resp = boto3.client("s3", region_name=region).list_objects_v2(
            Bucket=bucket, Prefix=prefix, MaxKeys=1
        )
        return bool(resp.get("Contents") or resp.get("CommonPrefixes"))
    except Exception:
        return False


def _safe_note_basename(name: str) -> Optional[str]:
    """Return a safe direct .md basename, or None if invalid."""
    cleaned = (name or "").replace("\\", "/").strip().lstrip("/")
    if not cleaned or "/" in cleaned or cleaned in {".", ".."} or ".." in cleaned:
        return None
    if not cleaned.lower().endswith(".md"):
        return None
    if cleaned.startswith("."):
        return None
    return cleaned


def list_folder_share_notes(folder_path: str) -> list[str]:
    """Basenames of direct ``.md`` children under ``folder_path`` (not recursive)."""
    cleaned = (folder_path or "").replace("\\", "/").lstrip("/")
    if not cleaned or ".." in cleaned.split("/"):
        return []
    names: set[str] = set()
    try:
        target = vault_backend.resolve_vault_path(cleaned)
        if target.is_dir():
            for child in target.iterdir():
                if (
                    child.is_file()
                    and child.suffix.lower() == ".md"
                    and not child.name.startswith(".")
                ):
                    names.add(child.name)
    except (ValueError, OSError):
        pass

    bucket, region = vault_backend.s3_bucket_and_region()
    if bucket:
        prefix = vault_backend.s3_prefix() + cleaned + "/"
        try:
            import boto3

            client = boto3.client("s3", region_name=region)
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
                for obj in page.get("Contents") or []:
                    key = str(obj.get("Key") or "")
                    if not key.startswith(prefix):
                        continue
                    rel = key[len(prefix) :]
                    if not rel or "/" in rel:
                        continue
                    if rel.lower().endswith(".md") and not rel.startswith("."):
                        names.add(rel)
        except Exception:
            pass

    return sorted(names, key=lambda s: s.lower())


def resolve_folder_note(folder_path: str, name: str) -> Optional[str]:
    """Resolve a direct child note under a folder share to a vault-relative path."""
    basename = _safe_note_basename(name)
    if not basename:
        return None
    folder = (folder_path or "").replace("\\", "/").lstrip("/")
    if not folder or ".." in folder.split("/"):
        return None
    full = f"{folder}/{basename}"
    if not vault_object_exists(full):
        return None
    return full


def path_under_folder(folder_path: str, rel_path: str) -> bool:
    """True if ``rel_path`` is strictly under ``folder_path`` (or equal to a child)."""
    folder = (folder_path or "").replace("\\", "/").lstrip("/")
    rel = (rel_path or "").replace("\\", "/").lstrip("/")
    if not folder or not rel or ".." in rel.split("/"):
        return False
    return rel == folder or rel.startswith(folder + "/")


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


def rewrite_md_assets_for_share(
    text: str,
    token: str,
    *,
    note_name: Optional[str] = None,
) -> str:
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
        if note_name:
            url = (
                f"/s/{quote(token, safe='')}/raw"
                f"?note={quote(note_name, safe='')}"
                f"&path={quote(inner, safe='')}"
            )
        else:
            url = f"/s/{quote(token, safe='')}/raw?path={quote(inner, safe='')}"
        if wrapped:
            return f"![{alt}](<{url}>)"
        return f"![{alt}]({url})"

    return re.sub(r"!\[([^\]]*)\]\(([^)\n]+)\)", repl, text or "")


_WIKI_LINK_RE = re.compile(
    r"(!)?\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]"
)


def _norm_wiki_key(name: str) -> str:
    s = (name or "").strip().replace("\\", "/")
    s = unicodedata.normalize("NFC", s)
    if s.lower().endswith(".md"):
        s = s[:-3]
    return s.lower()


def _slugify_heading(text: str) -> str:
    """Heading slug for share anchors — same rules as the public viewer."""
    from application.viewer_html import slugify_heading

    return slugify_heading(text)


def _folder_sibling_map(sibling_names: list[str]) -> dict[str, str]:
    """Map normalized stem / basename → exact ``Note.md`` filename."""
    by_key: dict[str, str] = {}
    for name in sibling_names:
        by_key[_norm_wiki_key(name)] = name
        by_key[_norm_wiki_key(Path(name).stem)] = name
    return by_key


def resolve_folder_share_wiki_target(
    target: str,
    sibling_names: list[str],
) -> Optional[str]:
    """Resolve ``[[target]]`` to a direct sibling ``.md`` basename, or None."""
    raw = (target or "").strip().replace("\\", "/")
    if not raw or ".." in raw.split("/"):
        return None
    by_key = _folder_sibling_map(sibling_names)
    key = _norm_wiki_key(raw)
    if key in by_key:
        return by_key[key]
    basename = _norm_wiki_key(raw.split("/")[-1])
    if basename in by_key:
        return by_key[basename]
    return None


def folder_sibling_full_path(folder_path: str, basename: str) -> str:
    folder = (folder_path or "").replace("\\", "/").lstrip("/")
    name = (basename or "").replace("\\", "/").lstrip("/")
    return f"{folder}/{name}" if folder else name


def is_folder_share_direct_child(folder_path: str, rel_path: str) -> bool:
    folder = (folder_path or "").replace("\\", "/").lstrip("/")
    rel = (rel_path or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        return False
    if not folder:
        return "/" not in rel and rel.lower().endswith(".md")
    prefix = folder + "/"
    if not rel.startswith(prefix):
        return False
    rest = rel[len(prefix) :]
    return bool(rest) and "/" not in rest and rest.lower().endswith(".md")


def folder_share_public_path(
    token: str,
    rel_path: str,
    *,
    folder_path: str,
) -> str:
    """``/n/{basename}`` for direct children, else ``/w/{vault-path}``."""
    rel = (rel_path or "").replace("\\", "/").lstrip("/")
    if is_folder_share_direct_child(folder_path, rel):
        return public_folder_note_path(token, Path(rel).name)
    return public_note_wiki_path(token, rel)


def folder_share_allowed_paths(
    folder_path: str,
    sibling_names: list[str],
) -> set[str]:
    """Direct folder children + one-hop wiki targets from those children only.

    External notes opened via ``/w/`` do not expand the allowed set further, so
    sharing a folder does not unlock the whole vault through chained hops.
    """
    from application import vault_index

    folder = (folder_path or "").replace("\\", "/").lstrip("/")
    allowed: set[str] = set()
    for name in sibling_names:
        full = folder_sibling_full_path(folder, name)
        allowed.add(full)
        text = read_vault_text(full) or ""
        for target in extract_wiki_link_targets(text):
            try:
                resolved = vault_index.resolve_link(target, from_path=full)
            except Exception:
                resolved = None
            if not resolved:
                continue
            cleaned = resolved.replace("\\", "/").lstrip("/")
            if not cleaned or ".." in cleaned.split("/"):
                continue
            if vault_object_exists(cleaned):
                allowed.add(cleaned)
    return allowed


def resolve_folder_share_link_target(
    target: str,
    *,
    from_path: str,
    folder_path: str,
    sibling_names: list[str],
    allowed_paths: set[str],
) -> Optional[str]:
    """Resolve wiki/md target to a vault path within the folder-share allowed set."""
    from application import vault_index

    raw = (target or "").strip().replace("\\", "/")
    if not raw or ".." in raw.split("/"):
        return None
    # Prefer same-folder sibling basename first (matches app UX).
    sib = resolve_folder_share_wiki_target(raw, sibling_names)
    if sib:
        full = folder_sibling_full_path(folder_path, sib)
        if full in allowed_paths:
            return full
    by_key = _allowed_path_map(allowed_paths)
    key = _norm_wiki_key(raw)
    if key in by_key:
        return by_key[key]
    basename = _norm_wiki_key(raw.split("/")[-1])
    if basename in by_key:
        return by_key[basename]
    try:
        resolved = vault_index.resolve_link(raw, from_path=from_path or None)
    except Exception:
        resolved = None
    if not resolved:
        return None
    cleaned = resolved.replace("\\", "/").lstrip("/")
    if cleaned in allowed_paths:
        return cleaned
    return None


def rewrite_wiki_links_for_folder_share(
    text: str,
    token: str,
    *,
    from_path: str,
    folder_path: str,
    sibling_names: list[str],
    allowed_paths: set[str],
) -> str:
    """Turn ``[[Note]]`` into ``/n/`` (sibling) or ``/w/`` (one-hop outside) links."""

    def repl(match: re.Match[str]) -> str:
        target = (match.group(2) or "").strip()
        heading = (match.group(3) or "").strip()
        alias = (match.group(4) or "").strip()
        label = alias or target
        resolved = resolve_folder_share_link_target(
            target,
            from_path=from_path,
            folder_path=folder_path,
            sibling_names=sibling_names,
            allowed_paths=allowed_paths,
        )
        if not resolved:
            return label
        url = folder_share_public_path(
            token, resolved, folder_path=folder_path
        )
        if heading:
            url = f"{url}#{_slugify_heading(heading)}"
        return f"[{label}]({url})"

    return _WIKI_LINK_RE.sub(repl, text or "")


def rewrite_md_note_links_for_folder_share(
    text: str,
    token: str,
    *,
    from_path: str,
    folder_path: str,
    sibling_names: list[str],
    allowed_paths: set[str],
) -> str:
    """Rewrite relative ``[label](Note.md)`` links within the folder-share allowed set."""

    def repl(match: re.Match[str]) -> str:
        label = match.group(1)
        dest = match.group(2).strip()
        if dest.startswith("<") and dest.endswith(">"):
            inner = dest[1:-1].strip()
        else:
            inner = dest
        if (
            not inner
            or inner.startswith("http://")
            or inner.startswith("https://")
            or inner.startswith("data:")
            or inner.startswith("/")
            or inner.startswith("#")
            or inner.startswith("/s/")
        ):
            return match.group(0)
        path_only = inner.split("#", 1)[0].strip()
        resolved = resolve_folder_share_link_target(
            path_only,
            from_path=from_path,
            folder_path=folder_path,
            sibling_names=sibling_names,
            allowed_paths=allowed_paths,
        )
        if not resolved:
            return match.group(0)
        url = folder_share_public_path(
            token, resolved, folder_path=folder_path
        )
        if "#" in inner:
            frag = inner.split("#", 1)[1]
            if frag:
                url = f"{url}#{_slugify_heading(frag)}"
        return f"[{label}]({url})"

    return re.sub(
        r"(?<!!)\[([^\]]*)\]\(([^)\n]+)\)",
        repl,
        text or "",
    )


def prepare_folder_share_note_markdown(
    text: str,
    token: str,
    *,
    note_path: str,
    folder_path: str,
    sibling_names: list[str],
    allowed_paths: set[str],
) -> str:
    """Wiki (siblings + one-hop) + assets for a folder-share note view."""
    out = rewrite_wiki_links_for_folder_share(
        text,
        token,
        from_path=note_path,
        folder_path=folder_path,
        sibling_names=sibling_names,
        allowed_paths=allowed_paths,
    )
    out = rewrite_md_note_links_for_folder_share(
        out,
        token,
        from_path=note_path,
        folder_path=folder_path,
        sibling_names=sibling_names,
        allowed_paths=allowed_paths,
    )
    out = rewrite_md_assets_for_note_share(out, token, doc_path=note_path)
    return out


def public_note_wiki_path(token: str, rel_path: str) -> str:
    """Public path for a wiki-linked note under a single-note share token."""
    cleaned = (rel_path or "").replace("\\", "/").lstrip("/")
    return f"/s/{quote(token, safe='')}/w/{quote(cleaned, safe='')}"


def note_share_public_path(token: str, rel_path: str, *, root_path: str) -> str:
    """URL for ``rel_path`` under a note share (root → ``/s/token``, else ``/w/``)."""
    root = (root_path or "").replace("\\", "/").lstrip("/")
    rel = (rel_path or "").replace("\\", "/").lstrip("/")
    if rel == root:
        return public_share_path(token)
    return public_note_wiki_path(token, rel)


def extract_wiki_link_targets(text: str) -> list[str]:
    """Return raw ``[[target]]`` strings (no aliases/headings) from markdown."""
    out: list[str] = []
    for match in _WIKI_LINK_RE.finditer(text or ""):
        target = (match.group(2) or "").strip()
        if target:
            out.append(target)
    return out


def note_share_allowed_paths(root_path: str, root_text: str) -> set[str]:
    """Vault paths reachable from a note share: the root note + its direct wiki targets.

    Does not recurse — only one hop from the published note — so the rest of the
    vault stays private unless separately shared.
    """
    from application import vault_index

    root = (root_path or "").replace("\\", "/").lstrip("/")
    allowed: set[str] = set()
    if root:
        allowed.add(root)
    for target in extract_wiki_link_targets(root_text):
        try:
            resolved = vault_index.resolve_link(target, from_path=root or None)
        except Exception:
            resolved = None
        if not resolved:
            continue
        cleaned = resolved.replace("\\", "/").lstrip("/")
        if ".." in cleaned.split("/"):
            continue
        if vault_object_exists(cleaned):
            allowed.add(cleaned)
    return allowed


def _allowed_path_map(allowed_paths: set[str]) -> dict[str, str]:
    by_key: dict[str, str] = {}
    for path in allowed_paths:
        by_key[_norm_wiki_key(path)] = path
        by_key[_norm_wiki_key(Path(path).stem)] = path
        by_key[_norm_wiki_key(Path(path).name)] = path
    return by_key


def resolve_note_share_wiki_target(
    target: str,
    *,
    from_path: str,
    allowed_paths: set[str],
) -> Optional[str]:
    """Resolve ``[[target]]`` to a vault path only if it is in ``allowed_paths``."""
    from application import vault_index

    raw = (target or "").strip().replace("\\", "/")
    if not raw or ".." in raw.split("/"):
        return None
    by_key = _allowed_path_map(allowed_paths)
    key = _norm_wiki_key(raw)
    if key in by_key:
        return by_key[key]
    basename = _norm_wiki_key(raw.split("/")[-1])
    if basename in by_key:
        return by_key[basename]
    try:
        resolved = vault_index.resolve_link(raw, from_path=from_path or None)
    except Exception:
        resolved = None
    if not resolved:
        return None
    cleaned = resolved.replace("\\", "/").lstrip("/")
    if cleaned in allowed_paths:
        return cleaned
    return None


def rewrite_wiki_links_for_note_share(
    text: str,
    token: str,
    *,
    from_path: str,
    root_path: str,
    allowed_paths: set[str],
) -> str:
    """Turn ``[[Note]]`` into links under the same note-share token (allowed set only)."""

    def repl(match: re.Match[str]) -> str:
        target = (match.group(2) or "").strip()
        heading = (match.group(3) or "").strip()
        alias = (match.group(4) or "").strip()
        label = alias or target
        resolved = resolve_note_share_wiki_target(
            target, from_path=from_path, allowed_paths=allowed_paths
        )
        if not resolved:
            return label
        url = note_share_public_path(token, resolved, root_path=root_path)
        if heading:
            url = f"{url}#{_slugify_heading(heading)}"
        return f"[{label}]({url})"

    return _WIKI_LINK_RE.sub(repl, text or "")


def rewrite_md_assets_for_note_share(
    text: str,
    token: str,
    *,
    doc_path: str,
) -> str:
    """Point relative images at ``/s/{token}/raw?doc=…&path=…`` for note shares."""

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
        url = (
            f"/s/{quote(token, safe='')}/raw"
            f"?doc={quote(doc_path, safe='')}"
            f"&path={quote(inner, safe='')}"
        )
        if wrapped:
            return f"![{alt}](<{url}>)"
        return f"![{alt}]({url})"

    return re.sub(r"!\[([^\]]*)\]\(([^)\n]+)\)", repl, text or "")


def prepare_note_share_markdown(
    text: str,
    token: str,
    *,
    note_path: str,
    root_path: str,
    allowed_paths: set[str],
) -> str:
    """Wiki links (allowed set) + assets for a single-note public share view."""
    out = rewrite_wiki_links_for_note_share(
        text,
        token,
        from_path=note_path,
        root_path=root_path,
        allowed_paths=allowed_paths,
    )
    out = rewrite_md_assets_for_note_share(out, token, doc_path=note_path)
    return out


def is_path_in_note_share_allowed(rel_path: str, allowed_paths: set[str]) -> bool:
    cleaned = (rel_path or "").replace("\\", "/").lstrip("/")
    return cleaned in allowed_paths
