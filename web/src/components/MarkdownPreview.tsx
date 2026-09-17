import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { api } from "../api";

const WIKI_RE = /(!)?\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]/g;

/** Hash prefix — relative URLs survive react-markdown's defaultUrlTransform
 *  (custom schemes like wiki:// are stripped to ""). */
export const WIKI_HASH_PREFIX = "#__wiki__/";

function expandWikiLinks(text: string): string {
  return text.replace(WIKI_RE, (_m, embed, target, _hash, alias) => {
    const label = alias || target.trim();
    if (embed) {
      return `*(embed: ${label})*`;
    }
    // Angle-bracket destination keeps spaces/parens safe for CommonMark.
    const dest = `${WIKI_HASH_PREFIX}${encodeURIComponent(target.trim())}`;
    return `[${label}](<${dest}>)`;
  });
}

/** Resolve a markdown image/link path relative to the note file. */
export function resolveNoteAssetPath(notePath: string, src: string): string {
  let cleaned = src.replace(/\\/g, "/").trim();
  try {
    cleaned = decodeURIComponent(cleaned);
  } catch {
    /* keep raw */
  }
  if (
    !cleaned ||
    cleaned.startsWith("/") ||
    cleaned.startsWith("http:") ||
    cleaned.startsWith("https:") ||
    cleaned.startsWith("data:")
  ) {
    return cleaned;
  }
  const dir = notePath.includes("/") ? notePath.slice(0, notePath.lastIndexOf("/")) : "";
  const parts = [...(dir ? dir.split("/") : []), ...cleaned.split("/")];
  const out: string[] = [];
  for (const part of parts) {
    if (!part || part === ".") continue;
    if (part === "..") {
      out.pop();
      continue;
    }
    out.push(part);
  }
  return out.join("/");
}

/** CommonMark rejects destinations with raw spaces unless wrapped in <...>. */
function normalizeMdMediaDestinations(text: string): string {
  return text.replace(/!\[([^\]]*)\]\(([^)\n]+)\)/g, (full, alt: string, dest: string) => {
    const d = dest.trim();
    if (d.startsWith("<") && d.endsWith(">")) return full;
    if (/\s/.test(d) || /[()]/.test(d)) {
      return `![${alt}](<${d.replace(/^<|>$/g, "")}>)`;
    }
    return full;
  });
}

function parseWikiHref(href: string | undefined): string | null {
  if (!href) return null;
  // react-markdown may leave "#__wiki__/..." or resolve against page as full URL
  const hashIdx = href.indexOf(WIKI_HASH_PREFIX);
  if (hashIdx >= 0) {
    const encoded = href.slice(hashIdx + WIKI_HASH_PREFIX.length);
    try {
      return decodeURIComponent(encoded);
    } catch {
      return encoded;
    }
  }
  if (href.startsWith("wiki://")) {
    try {
      return decodeURIComponent(href.slice("wiki://".length));
    } catch {
      return href.slice("wiki://".length);
    }
  }
  return null;
}

type Props = {
  content: string;
  notePath?: string | null;
  onWikiClick: (target: string) => void;
};

export function MarkdownPreview({ content, notePath, onWikiClick }: Props) {
  const expanded = normalizeMdMediaDestinations(expandWikiLinks(content));

  const components: Components = {
    a({ href, children }) {
      const wikiTarget = parseWikiHref(href);
      if (wikiTarget !== null) {
        return (
          <span
            className="wiki-link"
            role="link"
            tabIndex={0}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onWikiClick(wikiTarget);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onWikiClick(wikiTarget);
              }
            }}
          >
            {children}
          </span>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer">
          {children}
        </a>
      );
    },
    img({ src, alt }) {
      if (!src) return null;
      if (
        src.startsWith("http://") ||
        src.startsWith("https://") ||
        src.startsWith("data:") ||
        src.startsWith("/vault/")
      ) {
        return <img src={src} alt={alt || ""} />;
      }
      const resolved = notePath ? resolveNoteAssetPath(notePath, src) : src;
      return <img src={api.rawUrl(resolved)} alt={alt || ""} />;
    },
  };

  return (
    <div className="preview-pane">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={(url) => {
          if (url.includes(WIKI_HASH_PREFIX) || url.startsWith("wiki:")) return url;
          return defaultUrlTransform(url);
        }}
        components={components}
      >
        {expanded}
      </ReactMarkdown>
    </div>
  );
}
