"""Vault metadata cache: wiki-links, frontmatter, search, graph (derived data)."""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from application import vault_backend

logger = logging.getLogger("vault_index")

WIKI_LINK_RE = re.compile(
    r"(!)?\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]"
)
WORD_RE = re.compile(r"\S+")
FENCE_RE = re.compile(r"```[\s\S]*?```")
INLINE_CODE_RE = re.compile(r"`[^`]+`")


def _strip_code(text: str) -> str:
    text = FENCE_RE.sub(" ", text)
    return INLINE_CODE_RE.sub(" ", text)

_lock = threading.RLock()
_index: dict[str, "NoteMeta"] = {}
_name_map: dict[str, str] = {}  # lowercase name/alias -> relative path
_built = False


@dataclass
class NoteMeta:
    path: str
    title: str
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)  # target names (as written)
    embeds: list[str] = field(default_factory=list)
    word_count: int = 0
    char_count: int = 0
    mtime: float = 0.0
    body_preview: str = ""


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    raw = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")
    try:
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict):
            return {}, text
        return data, body
    except Exception:
        return {}, text


def _parse_note(rel: str, path: Path) -> NoteMeta:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = _split_frontmatter(text)
    title = str(fm.get("title") or "").strip()
    if not title:
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
    if not title:
        title = Path(rel).stem
    aliases_raw = fm.get("aliases") or []
    if isinstance(aliases_raw, str):
        aliases = [aliases_raw]
    elif isinstance(aliases_raw, list):
        aliases = [str(a) for a in aliases_raw]
    else:
        aliases = []
    tags_raw = fm.get("tags") or []
    if isinstance(tags_raw, str):
        tags = [tags_raw]
    elif isinstance(tags_raw, list):
        tags = [str(t) for t in tags_raw]
    else:
        tags = []

    links: list[str] = []
    embeds: list[str] = []
    for m in WIKI_LINK_RE.finditer(_strip_code(text)):
        is_embed = bool(m.group(1))
        target = m.group(2).strip()
        if is_embed:
            embeds.append(target)
        else:
            links.append(target)

    words = WORD_RE.findall(body)
    preview = body.strip().replace("\n", " ")[:240]
    return NoteMeta(
        path=rel,
        title=title,
        aliases=aliases,
        tags=tags,
        links=links,
        embeds=embeds,
        word_count=len(words),
        char_count=len(text),
        mtime=path.stat().st_mtime,
        body_preview=preview,
    )


def _register_names(meta: NoteMeta) -> None:
    stem = Path(meta.path).stem
    for name in {stem, meta.title, *meta.aliases}:
        key = name.strip().lower()
        if key:
            _name_map[key] = meta.path


def rebuild_index() -> dict[str, Any]:
    global _built
    root = vault_backend.vault_root()
    with _lock:
        _index.clear()
        _name_map.clear()
        for path in root.rglob("*.md"):
            if ".vault" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            try:
                meta = _parse_note(rel, path)
                _index[rel] = meta
                _register_names(meta)
            except Exception:
                logger.exception("Failed to index %s", rel)
        _built = True
        graph = build_graph_payload()
        cache = vault_backend.cache_dir()
        (cache / "index.json").write_text(
            json.dumps({k: meta.__dict__ for k, meta in _index.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (cache / "graph.json").write_text(
            json.dumps(graph, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        settings = vault_backend.settings_dir() / "graph.json"
        settings.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"notes": len(_index), "links": len(graph.get("edges", []))}


def ensure_index() -> None:
    global _built
    with _lock:
        if _built and _index:
            return
    rebuild_index()


def update_note(rel: str) -> None:
    root = vault_backend.vault_root()
    path = root / rel
    with _lock:
        # drop old name mappings for this path
        to_del = [k for k, v in _name_map.items() if v == rel]
        for k in to_del:
            del _name_map[k]
        if path.is_file() and path.suffix.lower() == ".md":
            meta = _parse_note(rel, path)
            _index[rel] = meta
            _register_names(meta)
        else:
            _index.pop(rel, None)
        _built = True


def remove_note(rel: str) -> None:
    with _lock:
        _index.pop(rel, None)
        to_del = [k for k, v in _name_map.items() if v == rel]
        for k in to_del:
            del _name_map[k]


def resolve_link(name: str) -> str | None:
    ensure_index()
    key = name.strip().lower()
    with _lock:
        if key in _name_map:
            return _name_map[key]
        # try with/without .md
        if key.endswith(".md") and key[:-3] in _name_map:
            return _name_map[key[:-3]]
        return None


def get_meta(rel: str) -> NoteMeta | None:
    ensure_index()
    with _lock:
        return _index.get(rel)


def list_metas() -> list[NoteMeta]:
    ensure_index()
    with _lock:
        return list(_index.values())


def backlinks(rel: str) -> list[dict[str, str]]:
    ensure_index()
    stem = Path(rel).stem.lower()
    meta = get_meta(rel)
    names = {stem}
    if meta:
        names.add(meta.title.lower())
        names.update(a.lower() for a in meta.aliases)
    results: list[dict[str, str]] = []
    with _lock:
        for other in _index.values():
            if other.path == rel:
                continue
            for link in other.links:
                if link.strip().lower() in names:
                    results.append({"path": other.path, "title": other.title})
                    break
    return results


def search(query: str, *, limit: int = 50) -> list[dict[str, Any]]:
    ensure_index()
    q = (query or "").strip().lower()
    if not q:
        return []
    hits: list[dict[str, Any]] = []
    root = vault_backend.vault_root()
    with _lock:
        metas = list(_index.values())
    for meta in metas:
        score = 0
        hay_title = f"{meta.title} {' '.join(meta.aliases)} {' '.join(meta.tags)} {meta.path}".lower()
        if q in hay_title:
            score += 10
        if q in meta.body_preview.lower():
            score += 3
        # full text scan for stronger match
        try:
            text = (root / meta.path).read_text(encoding="utf-8", errors="replace").lower()
            if q in text:
                score += 5
                idx = text.find(q)
                snippet = text[max(0, idx - 40) : idx + len(q) + 60].replace("\n", " ")
            else:
                snippet = meta.body_preview
        except OSError:
            snippet = meta.body_preview
        if score > 0:
            hits.append(
                {
                    "path": meta.path,
                    "title": meta.title,
                    "tags": meta.tags,
                    "score": score,
                    "snippet": snippet,
                }
            )
    hits.sort(key=lambda h: (-h["score"], h["title"].lower()))
    return hits[:limit]


def build_graph_payload() -> dict[str, Any]:
    nodes = []
    edges = []
    seen_edges: set[tuple[str, str]] = set()
    with _lock:
        for meta in _index.values():
            nodes.append(
                {
                    "id": meta.path,
                    "label": meta.title or Path(meta.path).stem,
                    "path": meta.path,
                    "tags": meta.tags,
                }
            )
            for link in meta.links:
                target = _name_map.get(link.strip().lower())
                if not target:
                    # unresolved — still show dangling node id as name
                    dangling_id = f"missing:{link}"
                    if not any(n["id"] == dangling_id for n in nodes):
                        nodes.append(
                            {
                                "id": dangling_id,
                                "label": link,
                                "path": None,
                                "tags": [],
                                "missing": True,
                            }
                        )
                    target = dangling_id
                key = (meta.path, target)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                edges.append({"source": meta.path, "target": target})
    return {"nodes": nodes, "edges": edges}


def get_graph() -> dict[str, Any]:
    ensure_index()
    with _lock:
        return build_graph_payload()
