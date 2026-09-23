"""CSP-safe HTML helpers for public markdown share pages (no inline scripts)."""

from __future__ import annotations

import html
import re


def _simple_markdown_to_html(text: str) -> str:
    escaped = html.escape(text or "")
    lines = escaped.splitlines()
    out: list[str] = []
    in_code = False
    in_ul = False

    def inline_format(line: str) -> str:
        rendered = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
        rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
        # Images then links (destinations already html-escaped from source).
        rendered = re.sub(
            r"!\[([^\]]*)\]\(([^)\n]+)\)",
            r'<img src="\2" alt="\1" />',
            rendered,
        )
        rendered = re.sub(
            r"\[([^\]]+)\]\(([^)\n]+)\)",
            r'<a href="\2">\1</a>',
            rendered,
        )
        return rendered

    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                if in_ul:
                    out.append("</ul>")
                    in_ul = False
                out.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            out.append(line + "\n")
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", line)
        if heading:
            if in_ul:
                out.append("</ul>")
                in_ul = False
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline_format(heading.group(2))}</h{level}>")
            continue
        if re.match(r"^[-*]\s+", line):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{inline_format(re.sub(r'^[-*]\s+', '', line))}</li>")
            continue
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if not line.strip():
            out.append("")
            continue
        out.append(f"<p>{inline_format(line)}</p>")
    if in_code:
        out.append("</code></pre>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def markdown_to_safe_html(text: str) -> str:
    try:
        import markdown as md_lib  # type: ignore

        return md_lib.markdown(
            text,
            extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
            output_format="html5",
        )
    except Exception:
        return _simple_markdown_to_html(text)


_MARKDOWN_BODY_CSS = """
    .markdown-body {
      background: transparent;
      color: #e6edf3;
      line-height: 1.6;
      font-size: 15px;
    }
    .markdown-body h1, .markdown-body h2, .markdown-body h3 {
      margin: 1.2em 0 0.5em;
      font-weight: 650;
      border-bottom: 1px solid #30363d;
      padding-bottom: 0.3em;
    }
    .markdown-body p { margin: 0.75em 0; }
    .markdown-body ul, .markdown-body ol { padding-left: 1.5em; }
    .markdown-body img {
      max-width: 100%;
      height: auto;
      border-radius: 6px;
    }
    .markdown-body code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 0.9em;
      background: rgba(110, 118, 129, 0.2);
      padding: 0.15em 0.4em;
      border-radius: 4px;
    }
    .markdown-body pre {
      overflow-x: auto;
      padding: 12px 14px;
      border-radius: 8px;
      background: rgba(110, 118, 129, 0.15);
      border: 1px solid #30363d;
    }
    .markdown-body pre code {
      background: transparent;
      padding: 0;
    }
    .markdown-body table {
      border-collapse: collapse;
      width: 100%;
      margin: 1em 0;
      font-size: 14px;
    }
    .markdown-body th, .markdown-body td {
      border: 1px solid #30363d;
      padding: 6px 10px;
      text-align: left;
    }
    .markdown-body a { color: #58a6ff; }
    .markdown-body blockquote {
      margin: 0.75em 0;
      padding: 0 1em;
      border-left: 3px solid #30363d;
      color: #8b949e;
    }
    @media (prefers-color-scheme: light) {
      .markdown-body { color: #1f2328; }
      .markdown-body h1, .markdown-body h2, .markdown-body h3,
      .markdown-body th, .markdown-body td,
      .markdown-body pre, .markdown-body blockquote {
        border-color: #d0d7de;
      }
      .markdown-body blockquote { color: #656d76; }
    }
"""


def build_markdown_viewer_page(
    file_name: str,
    text: str,
    *,
    topbar_right_html: str = "",
) -> str:
    title = html.escape(file_name)
    body_inner = markdown_to_safe_html(text)
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      margin: 0;
      background: #0d1117;
      color: #e6edf3;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    .topbar {{
      position: sticky; top: 0; z-index: 2;
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
      padding: 10px 20px;
      border-bottom: 1px solid #30363d;
      background: rgba(13, 17, 23, 0.92);
      backdrop-filter: blur(8px);
    }}
    .topbar h1 {{
      margin: 0; font-size: 14px; font-weight: 600;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }}
    .topbar-actions {{
      display: flex; align-items: center; gap: 14px; flex-shrink: 0;
    }}
    .topbar a.action {{
      color: #58a6ff; text-decoration: none; font-size: 13px; white-space: nowrap;
    }}
    .topbar a.action:hover {{ text-decoration: underline; }}
    .wrap {{
      box-sizing: border-box;
      max-width: 980px;
      margin: 0 auto;
      padding: 24px 20px 64px;
    }}
    {_MARKDOWN_BODY_CSS}
    @media (prefers-color-scheme: light) {{
      body {{ background: #ffffff; color: #1f2328; }}
      .topbar {{ background: rgba(255,255,255,0.92); border-bottom-color: #d0d7de; }}
    }}
  </style>
</head>
<body>
  <div class="topbar">
    <h1>{title}</h1>
    <div class="topbar-actions">{topbar_right_html}</div>
  </div>
  <div class="wrap">
    <article class="markdown-body">{body_inner}</article>
  </div>
</body>
</html>
"""


def build_folder_share_page(
    folder_title: str,
    notes: list[dict[str, str]],
    *,
    topbar_right_html: str = "",
) -> str:
    """Public index listing direct markdown notes under a shared folder.

    ``notes`` items: ``{"name": display stem or filename, "url": "/s/.../n/..."}``.
    """
    title = html.escape(folder_title)
    if notes:
        items_html = []
        for note in notes:
            name = html.escape(note.get("name") or "")
            href = html.escape(note.get("url") or "", quote=True)
            items_html.append(
                f'<li class="share-note-item">'
                f'<a class="share-note-link" href="{href}">{name}</a>'
                f"</li>"
            )
        list_html = '<ul class="share-note-list">' + "\n".join(items_html) + "</ul>"
    else:
        list_html = (
            '<p class="share-empty">이 폴더에 공유할 마크다운 노트가 없습니다.</p>'
        )
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      margin: 0;
      background: #0d1117;
      color: #e6edf3;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    .topbar {{
      position: sticky; top: 0; z-index: 2;
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
      padding: 10px 20px;
      border-bottom: 1px solid #30363d;
      background: rgba(13, 17, 23, 0.92);
      backdrop-filter: blur(8px);
    }}
    .topbar h1 {{
      margin: 0; font-size: 14px; font-weight: 600;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }}
    .topbar-actions {{
      display: flex; align-items: center; gap: 14px; flex-shrink: 0;
    }}
    .wrap {{
      box-sizing: border-box;
      max-width: 720px;
      margin: 0 auto;
      padding: 28px 20px 64px;
    }}
    .share-intro {{
      margin: 0 0 20px;
      color: #8b949e;
      font-size: 13px;
    }}
    .share-note-list {{
      list-style: none;
      margin: 0;
      padding: 0;
      border: 1px solid #30363d;
      border-radius: 10px;
      overflow: hidden;
    }}
    .share-note-item {{
      border-bottom: 1px solid #30363d;
    }}
    .share-note-item:last-child {{ border-bottom: none; }}
    .share-note-link {{
      display: block;
      padding: 14px 16px;
      color: #58a6ff;
      text-decoration: none;
      font-size: 15px;
      font-weight: 500;
    }}
    .share-note-link:hover {{
      background: rgba(56, 139, 253, 0.08);
      text-decoration: underline;
    }}
    .share-empty {{
      margin: 0;
      padding: 24px 16px;
      color: #8b949e;
      font-size: 14px;
      text-align: center;
      border: 1px dashed #30363d;
      border-radius: 10px;
    }}
    @media (prefers-color-scheme: light) {{
      body {{ background: #ffffff; color: #1f2328; }}
      .topbar {{ background: rgba(255,255,255,0.92); border-bottom-color: #d0d7de; }}
      .share-intro {{ color: #656d76; }}
      .share-note-list {{ border-color: #d0d7de; }}
      .share-note-item {{ border-bottom-color: #d0d7de; }}
      .share-note-link:hover {{ background: rgba(9, 105, 218, 0.06); }}
      .share-empty {{ color: #656d76; border-color: #d0d7de; }}
    }}
  </style>
</head>
<body>
  <div class="topbar">
    <h1>{title}</h1>
    <div class="topbar-actions">{topbar_right_html}</div>
  </div>
  <div class="wrap">
    <p class="share-intro">Shared folder — direct notes only</p>
    {list_html}
  </div>
</body>
</html>
"""


def build_text_viewer_page(
    file_name: str,
    text: str,
    *,
    as_markdown: bool,
    download_href: str = "",
) -> str:
    """CSP-safe text/markdown viewer for Load-files ``/api/files/view``."""
    download_link = ""
    if download_href:
        download_link = (
            f'<a class="action" href="{html.escape(download_href, quote=True)}">'
            "Download</a>"
        )
    if as_markdown:
        return build_markdown_viewer_page(
            file_name, text, topbar_right_html=download_link
        )

    title = html.escape(file_name)
    body_inner = f'<pre class="code">{html.escape(text)}</pre>'
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      margin: 0;
      background: #0d1117;
      color: #e6edf3;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    }}
    .topbar {{
      position: sticky; top: 0; z-index: 2;
      display: flex; align-items: center; justify-content: space-between; gap: 12px;
      padding: 10px 20px;
      border-bottom: 1px solid #30363d;
      background: rgba(13, 17, 23, 0.92);
      backdrop-filter: blur(8px);
    }}
    .topbar h1 {{
      margin: 0; font-size: 14px; font-weight: 600;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }}
    .topbar a.action {{
      color: #58a6ff; text-decoration: none; font-size: 13px; white-space: nowrap;
    }}
    .wrap {{
      box-sizing: border-box;
      max-width: 980px;
      margin: 0 auto;
      padding: 24px 20px 64px;
    }}
    pre.code {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 13px;
      line-height: 1.5;
    }}
    @media (prefers-color-scheme: light) {{
      body {{ background: #ffffff; color: #1f2328; }}
      .topbar {{ background: rgba(255,255,255,0.92); border-bottom-color: #d0d7de; }}
    }}
  </style>
</head>
<body>
  <div class="topbar">
    <h1>{title}</h1>
    {download_link}
  </div>
  <div class="wrap">
    <article>{body_inner}</article>
  </div>
</body>
</html>
"""
