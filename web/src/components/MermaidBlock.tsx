import { useEffect, useId, useState } from "react";
import mermaid from "mermaid";

let mermaidReady = false;

function ensureMermaid() {
  if (mermaidReady) return;
  const theme =
    document.documentElement.getAttribute("data-theme") === "light"
      ? "default"
      : "dark";
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "loose",
    theme,
    fontFamily: "inherit",
  });
  mermaidReady = true;
}

type Props = {
  chart: string;
};

export function MermaidBlock({ chart }: Props) {
  const reactId = useId().replace(/:/g, "");
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const source = chart.trim();
    if (!source) {
      setSvg(null);
      setError(null);
      return;
    }

    void (async () => {
      try {
        ensureMermaid();
        const id = `mermaid-${reactId}-${Math.random().toString(36).slice(2, 8)}`;
        const { svg: rendered } = await mermaid.render(id, source);
        if (!cancelled) {
          setSvg(rendered);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setSvg(null);
          setError(err instanceof Error ? err.message : String(err));
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [chart, reactId]);

  if (error) {
    return (
      <div className="mermaid-block mermaid-error" role="alert">
        <p className="mermaid-error-msg">Mermaid 렌더 실패: {error}</p>
        <pre>
          <code>{chart}</code>
        </pre>
      </div>
    );
  }

  if (!svg) {
    return (
      <div className="mermaid-block mermaid-loading" aria-busy="true">
        다이어그램 렌더링 중…
      </div>
    );
  }

  return (
    <div
      className="mermaid-block"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
