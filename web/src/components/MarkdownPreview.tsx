import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

const WIKI_RE = /(!)?\[\[([^\]|#]+)(?:#([^\]|]+))?(?:\|([^\]]+))?\]\]/g;

function expandWikiLinks(text: string): string {
  return text.replace(WIKI_RE, (_m, embed, target, _hash, alias) => {
    const label = alias || target;
    if (embed) {
      return `*(embed: ${label})*`;
    }
    // use a markdown link with special protocol for click handling
    return `[${label}](wiki://${encodeURIComponent(target)})`;
  });
}

type Props = {
  content: string;
  onWikiClick: (target: string) => void;
};

export function MarkdownPreview({ content, onWikiClick }: Props) {
  const expanded = expandWikiLinks(content);

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
  };

  return (
    <div className="preview-pane">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {expanded}
      </ReactMarkdown>
    </div>
  );
}
