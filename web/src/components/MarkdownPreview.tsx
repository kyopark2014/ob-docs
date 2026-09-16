import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";
import { api } from "../api";

const WIKI_RE = /(!)?\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]/g;

function expandWikiLinks(text: string): string {
  return text.replace(WIKI_RE, (_m, embed, target, _hash, alias) => {
    const label = alias || target;
    if (embed) {
      return `*(embed: ${label})*`;
    }
    return `[${label}](wiki://${encodeURIComponent(target)})`;
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

type Props = {
  content: string;
  notePath?: string | null;
  onWikiClick: (target: string) => void;
};

export function MarkdownPreview({ content, notePath, onWikiClick }: Props) {
  const expanded = normalizeMdMediaDestinations(expandWikiLinks(content));

  const components: Components = {
    a({ href, children }) {
      if (href?.startsWith("wiki://")) {
        const target = decodeURIComponent(href.slice("wiki://".length));
        return (
          <span
            className="wiki-link"
            role="link"
            tabIndex={0}
            onClick={(e) => {
              e.preventDefault();
              onWikiClick(target);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") onWikiClick(target);
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
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {expanded}
      </ReactMarkdown>
    </div>
  );
}
