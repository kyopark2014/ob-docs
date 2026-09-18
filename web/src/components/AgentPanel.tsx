import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type AgentChatHandlers, type AgentToolEvent } from "../api";
import {
  AgentChatInput,
  type AgentNoteChip,
  type AgentSendPayload,
} from "./AgentChatInput";
import { ToolCallCard } from "./ToolCallCard";

export type AgentMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolEvents?: AgentToolEvent[];
};

type Props = {
  notePath: string | null;
  modelName?: string | null;
  onClose: () => void;
  onNoteUpdated?: (path: string) => void;
  onResizeStart?: (e: ReactPointerEvent<HTMLDivElement>) => void;
  onResizeReset?: () => void;
  resizing?: boolean;
};

function newId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

/** Client-side safety net: never render VAULT_WRITE marker bodies in chat. */
function stripVaultWriteMarkers(text: string): string {
  const complete =
    /<<<VAULT_WRITE\s+.+?>>>\s*\n?[\s\S]*?<<<END_VAULT_WRITE>>>/g;
  const incomplete = /<<<VAULT_WRITE\b[\s\S]*$/;
  return text.replace(complete, "").replace(incomplete, "").trim();
}

function upsertStreamEvent(
  prev: AgentToolEvent[],
  event: AgentToolEvent,
): AgentToolEvent[] {
  if (event.type === "text") {
    const data = stripVaultWriteMarkers(event.data || "");
    if (!data) return prev;
    return [...prev, { type: "text", data }];
  }
  const tid = (event.toolUseId || "").trim();
  if (!tid) return [...prev, event];
  const next = [...prev];
  for (let i = next.length - 1; i >= 0; i -= 1) {
    if (next[i].type === event.type && next[i].toolUseId === tid) {
      next[i] = { ...next[i], ...event };
      return next;
    }
  }
  next.push(event);
  return next;
}

function normalizeText(value: string): string {
  return value.trim().replace(/\s+/g, " ");
}

function isStreamingPrefixOfFinal(partial: string, finalText: string): boolean {
  if (!partial || !finalText) return false;
  if (finalText.startsWith(partial) || partial.startsWith(finalText)) return true;
  const headLen = Math.min(partial.length, finalText.length, 80);
  return partial.slice(0, headLen) === finalText.slice(0, headLen);
}

/** Hide early text segments that were superseded by the final reply (harness-work). */
function filterSupersededTextEvents(
  events: AgentToolEvent[],
  content: string,
): AgentToolEvent[] {
  const normalizedContent = normalizeText(content);
  const textIndexes = events
    .map((event, index) => (event.type === "text" ? index : -1))
    .filter((index) => index >= 0);
  const hidden = new Set<number>();

  for (let i = 0; i < textIndexes.length; i += 1) {
    const index = textIndexes[i];
    const text = normalizeText(events[index].data ?? "");
    for (let j = i + 1; j < textIndexes.length; j += 1) {
      const laterIndex = textIndexes[j];
      const later = normalizeText(events[laterIndex].data ?? "");
      if (isStreamingPrefixOfFinal(text, later) && text.length < later.length) {
        hidden.add(index);
        break;
      }
    }
    if (
      !hidden.has(index) &&
      normalizedContent &&
      isStreamingPrefixOfFinal(text, normalizedContent) &&
      text.length < normalizedContent.length
    ) {
      hidden.add(index);
    }
  }

  return events.filter((_, index) => !hidden.has(index));
}

function MessageTimeline({
  content,
  toolEvents,
  liveText,
  streaming,
}: {
  content?: string;
  toolEvents?: AgentToolEvent[];
  liveText?: string;
  streaming?: boolean;
}) {
  const rawEvents = toolEvents || [];
  const events = filterSupersededTextEvents(rawEvents, content || "");
  const hasToolCards = events.some(
    (e) => e.type === "tool" || e.type === "tool_result" || e.type === "info",
  );
  const hasTextEvent = events.some(
    (e) => e.type === "text" && (e.data || "").trim(),
  );
  const normalizedContent = normalizeText(content || "");
  const contentCoveredByTimeline = events.some(
    (event) =>
      event.type === "text" &&
      normalizeText(event.data ?? "") === normalizedContent,
  );
  const showTrailingContent =
    !liveText &&
    normalizedContent.length > 0 &&
    !contentCoveredByTimeline &&
    !hasTextEvent;
  const showThinking =
    !!streaming && !liveText?.trim() && !showTrailingContent && !hasToolCards;

  if (!hasToolCards && !hasTextEvent && !liveText && !showTrailingContent) {
    if (streaming) {
      return (
        <div className="agent-streaming-indicator" aria-label="Thinking">
          Thinking
          <span className="agent-thinking-ellipsis" aria-hidden="true" />
        </div>
      );
    }
    return null;
  }

  return (
    <div className="agent-message-timeline">
      {events.map((event, index) => {
        if (event.type === "text") {
          const text = event.data || "";
          if (!text.trim()) return null;
          return (
            <div key={`text-${index}`} className="agent-message-bubble">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
            </div>
          );
        }
        return (
          <ToolCallCard
            key={`${event.type}-${event.toolUseId || index}`}
            event={event}
          />
        );
      })}
      {liveText ? (
        <div className="agent-message-bubble">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{liveText}</ReactMarkdown>
        </div>
      ) : null}
      {showTrailingContent ? (
        <div className="agent-message-bubble">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content!}</ReactMarkdown>
        </div>
      ) : null}
      {showThinking ? (
        <div className="agent-streaming-indicator" aria-label="Thinking">
          Thinking
          <span className="agent-thinking-ellipsis" aria-hidden="true" />
        </div>
      ) : null}
    </div>
  );
}

export function AgentPanel({
  notePath,
  modelName,
  onClose,
  onNoteUpdated,
  onResizeStart,
  onResizeReset,
  resizing = false,
}: Props) {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState("");
  const [streamEvents, setStreamEvents] = useState<AgentToolEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [note, setNote] = useState<AgentNoteChip | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamTextRef = useRef("");

  const flushLiveTextIntoEvents = useCallback(() => {
    const cleaned = stripVaultWriteMarkers(streamTextRef.current);
    streamTextRef.current = "";
    setStreamText("");
    if (!cleaned) return;
    setStreamEvents((prev) =>
      upsertStreamEvent(prev, { type: "text", data: cleaned }),
    );
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!notePath) {
      setNote(null);
      return;
    }
    void (async () => {
      try {
        const meta = await api.agentNoteMeta(notePath);
        if (cancelled) return;
        setNote({ path: meta.path, name: meta.name, size: meta.size });
      } catch {
        if (cancelled) return;
        const name = notePath.split("/").pop() || notePath;
        setNote({ path: notePath, name, size: 0 });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [notePath]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: streaming ? "auto" : "smooth" });
  }, [messages, streamText, streamEvents, streaming]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreaming(false);
  }, []);

  const send = useCallback(
    async (payload: AgentSendPayload) => {
      const text = (payload.text || "").trim();
      const imagePaths = payload.imagePaths || [];
      const filePaths = payload.filePaths || [];
      const display =
        text ||
        (imagePaths.length || filePaths.length
          ? [
              imagePaths.length ? `이미지 ${imagePaths.length}개` : "",
              filePaths.length ? `파일 ${filePaths.length}개` : "",
            ]
              .filter(Boolean)
              .join(", ")
          : "");
      setError(null);
      const userMsg: AgentMessage = {
        id: newId(),
        role: "user",
        content: display || "(첨부)",
      };
      setMessages((prev) => [...prev, userMsg]);
      setStreaming(true);
      setStreamText("");
      streamTextRef.current = "";
      setStreamEvents([]);

      const ac = new AbortController();
      abortRef.current = ac;

      const handlers: AgentChatHandlers = {
        onSession: (id) => setSessionId(id),
        onToken: (t) => {
          const cleaned = stripVaultWriteMarkers(t);
          streamTextRef.current = cleaned;
          setStreamText(cleaned);
        },
        onText: (data) => {
          setStreamEvents((prev) =>
            upsertStreamEvent(prev, { type: "text", data }),
          );
          streamTextRef.current = "";
          setStreamText("");
        },
        onTool: (event) => {
          flushLiveTextIntoEvents();
          setStreamEvents((prev) => upsertStreamEvent(prev, event));
        },
        onToolResult: (event) => {
          setStreamEvents((prev) => upsertStreamEvent(prev, event));
        },
        onNoteUpdated: (path) => onNoteUpdated?.(path),
        onDone: (result, id, toolEvents) => {
          if (id) setSessionId(id);
          const content =
            stripVaultWriteMarkers(result || "").trim() || "(응답 없음)";
          const cleanedEvents = (toolEvents || [])
            .map((ev) =>
              ev.type === "text"
                ? { ...ev, data: stripVaultWriteMarkers(ev.data || "") }
                : ev,
            )
            .filter((ev) => ev.type !== "text" || (ev.data || "").trim());
          setMessages((prev) => [
            ...prev,
            {
              id: newId(),
              role: "assistant",
              content,
              toolEvents: cleanedEvents.length > 0 ? cleanedEvents : undefined,
            },
          ]);
          streamTextRef.current = "";
          setStreamText("");
          setStreamEvents([]);
          setStreaming(false);
          abortRef.current = null;
        },
        onError: (msg) => {
          setError(msg);
          streamTextRef.current = "";
          setStreamText("");
          setStreamEvents([]);
          setStreaming(false);
          abortRef.current = null;
        },
      };

      try {
        await api.agentChat(
          {
            prompt: text,
            note_path: note?.path ?? null,
            session_id: sessionId,
            model_name: modelName || null,
            image_paths: imagePaths,
            file_paths: filePaths,
          },
          handlers,
          ac.signal,
        );
      } catch (err) {
        if (ac.signal.aborted) {
          setStreaming(false);
          setStreamText("");
          setStreamEvents([]);
          return;
        }
        setError(err instanceof Error ? err.message : String(err));
        setStreaming(false);
        setStreamText("");
        setStreamEvents([]);
        abortRef.current = null;
      }
    },
    [note?.path, modelName, onNoteUpdated, sessionId, flushLiveTextIntoEvents],
  );

  return (
    <aside className="agent-panel" aria-label="Open agent">
      {onResizeStart && (
        <div
          className={`agent-panel-resizer${resizing ? " is-active" : ""}`}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize agent panel"
          title="Drag to resize · double-click to reset"
          onPointerDown={onResizeStart}
          onDoubleClick={onResizeReset}
        />
      )}
      <header className="agent-panel-header">
        <span className="agent-panel-title">Agent</span>
        {modelName ? (
          <span className="agent-panel-model" title={modelName}>
            {modelName}
          </span>
        ) : null}
        <button
          type="button"
          className="agent-panel-close"
          aria-label="에이전트 닫기"
          onClick={() => {
            stop();
            onClose();
          }}
        >
          ×
        </button>
      </header>
      <div className="agent-chat-scroll">
        <div className="agent-chat-thread">
          {messages.length === 0 && !streaming && (
            <div className="agent-empty-state">
              <p>선택한 노트를 수정·요약하도록 요청하세요.</p>
              <p>use-vault skill · websearch(exa) · VAULT_WRITE 저장</p>
            </div>
          )}
          {messages.map((m) => (
            <div key={m.id} className={`agent-message-row ${m.role}`}>
              {m.role === "assistant" && m.toolEvents && m.toolEvents.length > 0 ? (
                <MessageTimeline content={m.content} toolEvents={m.toolEvents} />
              ) : (
                <div className="agent-message-bubble">
                  {m.role === "assistant" ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                  ) : (
                    m.content
                  )}
                </div>
              )}
            </div>
          ))}
          {streaming && (
            <div className="agent-message-row assistant">
              <MessageTimeline
                toolEvents={streamEvents}
                liveText={streamText}
                streaming
              />
            </div>
          )}
          {error && (
            <div className="agent-chat-error" role="alert">
              {error}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>
      <AgentChatInput
        note={note}
        notePath={notePath}
        waiting={streaming}
        onRemoveNote={() => setNote(null)}
        onSend={(payload) => void send(payload)}
        onStop={stop}
      />
    </aside>
  );
}
