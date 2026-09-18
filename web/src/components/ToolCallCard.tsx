import type { AgentToolEvent } from "../api";

interface Props {
  event: AgentToolEvent;
}

function formatToolInput(input: unknown): string {
  if (input === undefined || input === null) {
    return "(매개변수 없음)";
  }
  if (typeof input === "object" && !Array.isArray(input)) {
    const keys = Object.keys(input as Record<string, unknown>);
    if (keys.length === 0) {
      return "(매개변수 없음)";
    }
  }
  return JSON.stringify(input, null, 2);
}

function formatToolPayload(data: string | undefined): string {
  if (!data) return "";
  try {
    const parsed = JSON.parse(data) as unknown;
    if (Array.isArray(parsed)) {
      const texts = parsed
        .filter(
          (block): block is { type: string; text: string } =>
            !!block &&
            typeof block === "object" &&
            (block as { type?: unknown }).type === "text" &&
            typeof (block as { text?: unknown }).text === "string",
        )
        .map((block) => block.text);
      if (texts.length > 0) {
        return texts.join("\n\n");
      }
    }
    return JSON.stringify(parsed, null, 2);
  } catch {
    return data.replace(/\\n/g, "\n").replace(/\\t/g, "\t");
  }
}

function formatToolLabel(tool?: string): string {
  if (tool === "vault_write") return "Tools: vault_write (노트 저장)";
  return tool ? `Tools: ${tool}` : "Tools";
}

function formatToolResultLabel(tool?: string): string {
  if (tool === "vault_write") return "Tool result: vault_write";
  return tool ? `Tool result: ${tool}` : "Tool result";
}

export function ToolCallCard({ event }: Props) {
  if (event.type === "tool") {
    return (
      <details className="tool-card">
        <summary>{formatToolLabel(event.tool)}</summary>
        <pre>{formatToolInput(event.input)}</pre>
      </details>
    );
  }
  if (event.type === "tool_result") {
    return (
      <details className="tool-card">
        <summary>{formatToolResultLabel(event.tool)}</summary>
        <pre>{formatToolPayload(event.data)}</pre>
      </details>
    );
  }
  return (
    <details className="tool-card">
      <summary>Info</summary>
      <pre>{formatToolPayload(event.data)}</pre>
    </details>
  );
}
