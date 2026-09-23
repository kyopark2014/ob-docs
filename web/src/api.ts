import type { FilePayload, GraphPayload, SearchHit, TreeNode } from "./types";

export type ShareEntry = {
  token: string;
  path: string;
  title: string;
  type?: "note" | "folder";
  created_at: number;
  url_path: string;
  url?: string;
};

export type GraphPattern = "pattern1" | "pattern2" | "pattern3";

export type NotesFolderOption = {
  name: string;
  path: string;
};

export type NotesGraphStatus = {
  notes_dir: string;
  folders?: string[];
  include_missing?: boolean;
  available_folders?: NotesFolderOption[];
  exists: boolean;
  path?: string | null;
  storage?: string;
  status: string;
  pattern?: GraphPattern | string;
  error?: string | null;
  message?: string | null;
  last_success_at?: string | null;
  progress?: {
    file?: string | null;
    file_i?: number | null;
    file_n?: number | null;
    pct?: number | null;
    phase?: string | null;
  } | null;
};

export type NotesSourcesConfig = {
  notes_dir: string;
  folders: string[];
  include_missing: boolean;
  available_folders: NotesFolderOption[];
  max_sources: number;
};

const BASE = "/api";

async function readResponseBody(res: Response): Promise<unknown> {
  const raw = await res.text();
  if (!raw) return null;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return raw;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });
  const body = await readResponseBody(res);
  if (!res.ok) {
    const message = formatApiError(body, res.status);
    const err = new Error(message) as Error & { status?: number; detail?: unknown };
    err.status = res.status;
    err.detail = body;
    throw err;
  }
  return body as T;
}

function formatApiError(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail.trim()) return detail.trim();
  if (detail && typeof detail === "object") {
    const root = detail as { detail?: unknown; message?: unknown; error?: unknown };
    const inner = root.detail !== undefined ? root.detail : root;
    if (typeof inner === "string" && inner.trim()) return inner.trim();
    if (inner && typeof inner === "object") {
      const obj = inner as { message?: unknown; error?: unknown; mode?: unknown };
      if (typeof obj.message === "string" && obj.message.trim()) {
        if (obj.error === "s3_unavailable" || obj.mode === "local") {
          return "로컬 모드에서는 S3 동기화를 사용할 수 없습니다. VAULT_S3_ENABLE=1 로 실행하세요.";
        }
        return obj.message.trim();
      }
      if (typeof obj.error === "string" && obj.error.trim()) return obj.error.trim();
    }
    if (typeof root.message === "string" && root.message.trim()) return root.message.trim();
  }
  return `HTTP ${status}`;
}

export const api = {
  getSession: () =>
    request<{ user_id: string; sharing_url: string; authenticated: boolean }>(
      "/session",
    ),
  createLocalSession: (user_id = "local-dev") =>
    request<{ user_id: string; sharing_url: string }>("/session", {
      method: "POST",
      body: JSON.stringify({ user_id }),
    }),
  setSessionWithAccessToken: (access_token: string) =>
    request<{ user_id: string; sharing_url: string; authenticated: boolean }>(
      "/session",
      {
        method: "POST",
        body: JSON.stringify({ access_token }),
      },
    ),
  loginWithCognito: (username: string, password: string) =>
    request<{ user_id: string; sharing_url: string; authenticated: boolean }>(
      "/session",
      {
        method: "POST",
        body: JSON.stringify({ username, password }),
      },
    ),
  getPublicConfig: () =>
    request<{
      auth_mode: "google" | "cognito";
      google_client_id: string;
      local_auth_bypass: boolean;
      sharing_url: string;
      project_name: string;
      cognito_admin_username: string;
    }>("/config"),
  clearSession: () =>
    request<{ ok: boolean }>("/session", { method: "DELETE" }),
  getTree: () =>
    request<{ root: string; mode: string; children: TreeNode[] }>("/files/tree"),
  readFile: (path: string) =>
    request<FilePayload>(`/files/read?path=${encodeURIComponent(path)}`),
  writeFile: (path: string, content: string) =>
    request<{ ok: boolean; path: string; word_count?: number; char_count?: number }>(
      "/files/write",
      { method: "PUT", body: JSON.stringify({ path, content }) },
    ),
  mkdir: (path: string) =>
    request<{ ok: boolean }>("/files/mkdir", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  rename: (from_path: string, to_path: string) =>
    request<{ ok: boolean; from: string; to: string }>("/files/rename", {
      method: "POST",
      body: JSON.stringify({ from_path, to_path }),
    }),
  reorderFolder: (folder: string, names: string[]) =>
    request<{ ok: boolean; folder: string; names: string[] }>("/files/order", {
      method: "PUT",
      body: JSON.stringify({ folder, names }),
    }),
  duplicate: (path: string) =>
    request<{ ok: boolean; from: string; to: string }>("/files/duplicate", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  createShare: (path: string) =>
    request<{
      ok: boolean;
      token: string;
      path: string;
      title: string;
      type?: "note" | "folder";
      created_at?: number;
      url_path: string;
      url?: string;
    }>("/files/share", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  listShares: () =>
    request<{ ok: boolean; count: number; shares: ShareEntry[] }>("/files/shares"),
  deleteShare: (token: string) =>
    request<{ ok: boolean; token: string }>("/files/share/delete", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  getSharePermission: () =>
    request<{
      ok: boolean;
      permission: string;
      options: string[];
      default: string;
    }>("/files/share/permission"),
  setSharePermission: (permission: string) =>
    request<{ ok: boolean; permission: string }>("/files/share/permission", {
      method: "PUT",
      body: JSON.stringify({ permission }),
    }),
  deletePath: (path: string) =>
    request<{ ok: boolean }>("/files/delete", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  uploadFile: async (path: string, blob: Blob, filename?: string) => {
    const fd = new FormData();
    fd.append("path", path);
    fd.append("file", blob, filename || path.split("/").pop() || "upload.bin");
    const res = await fetch(`${BASE}/files/upload`, {
      method: "POST",
      credentials: "include",
      body: fd,
    });
    const body = await readResponseBody(res);
    if (!res.ok) {
      const err = new Error(
        typeof body === "object" && body && "detail" in body
          ? JSON.stringify((body as { detail: unknown }).detail)
          : formatApiError(body, res.status),
      ) as Error & { status?: number; detail?: unknown };
      err.status = res.status;
      err.detail = body;
      throw err;
    }
    return body as { ok: boolean; path: string; size: number };
  },
  rawUrl: (path: string) =>
    `${BASE}/files/raw?path=${encodeURIComponent(path)}`,
  /** Authenticated HTML viewer (new tab) for vault attachments — agentic-work style. */
  viewUrl: (path: string) =>
    `${BASE}/files/view?path=${encodeURIComponent(path)}`,
  search: (q: string) =>
    request<{ query: string; results: SearchHit[] }>(
      `/search?q=${encodeURIComponent(q)}`,
    ),
  resolveWikiLink: (target: string, fromPath?: string | null) => {
    const qs = new URLSearchParams({ target });
    if (fromPath) qs.set("from", fromPath);
    return request<{ target: string; from: string | null; path: string | null }>(
      `/search/resolve?${qs.toString()}`,
    );
  },
  getGraph: () => request<GraphPayload>("/graph"),
  getNotesGraphStatus: () => request<NotesGraphStatus>("/graph/status"),
  syncNotesGraph: (full = false) =>
    request<NotesGraphStatus>(`/graph/sync${full ? "?full=1" : ""}`, {
      method: "POST",
    }),
  rebuildGraph: () =>
    request<NotesGraphStatus & { ok?: boolean; notes?: number; links?: number }>(
      "/graph/rebuild",
      { method: "POST" },
    ),
  setNotesGraphPattern: (pattern: GraphPattern | string) =>
    request<NotesGraphStatus>("/graph/pattern", {
      method: "PATCH",
      body: JSON.stringify({ pattern }),
    }),
  getNotesGraphSources: () => request<NotesSourcesConfig>("/graph/sources"),
  putNotesGraphSources: (body: {
    folders: string[];
    include_missing?: boolean;
  }) =>
    request<NotesSourcesConfig>("/graph/sources", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  getSyncStatus: () =>
    request<{
      mode: string;
      pending: number;
      status?: string;
      busy?: boolean;
      message?: string | null;
      error?: string | null;
      progress?: {
        file?: string | null;
        file_i?: number | null;
        file_n?: number | null;
        pct?: number | null;
        phase?: string | null;
      } | null;
      ops: Array<{ op: string; path?: string }>;
    }>("/files/sync"),
  syncVault: () =>
    request<{
      ok?: boolean;
      status: string;
      busy?: boolean;
      message?: string;
      pending?: number;
    }>("/files/sync", { method: "POST" }),
  agentHealth: () =>
    request<{
      status: string;
      harnessConfigured: boolean;
      harnessArn?: string | null;
    }>("/agent/health"),
  agentNoteMeta: (path: string) =>
    request<{
      path: string;
      name: string;
      size: number;
      char_count: number;
      note_id?: string | null;
      title?: string | null;
      size_bytes?: number;
      created_at?: string | null;
      updated_at?: string | null;
    }>(`/agent/note-meta?path=${encodeURIComponent(path)}`),
  listNotes: () =>
    request<{
      count: number;
      notes: {
        note_id: string;
        title: string;
        path: string;
        size_bytes: number;
        created_at: string;
        updated_at: string;
      }[];
    }>("/files/notes"),
  agentMessages: (opts: { noteId?: string | null; notePath?: string | null }) => {
    const q = new URLSearchParams();
    if (opts.noteId) q.set("note_id", opts.noteId);
    if (opts.notePath) q.set("note_path", opts.notePath);
    return request<{
      note_id: string;
      count: number;
      messages: Array<{
        id: string;
        note_id: string;
        role: "user" | "assistant";
        content: string;
        attachments: string[];
        tool_events: AgentToolEvent[];
        created_at: string;
      }>;
    }>(`/agent/messages?${q.toString()}`);
  },
  clearAgentMessages: (opts: { noteId?: string | null; notePath?: string | null }) => {
    const q = new URLSearchParams();
    if (opts.noteId) q.set("note_id", opts.noteId);
    if (opts.notePath) q.set("note_path", opts.notePath);
    return request<{ ok: boolean; note_id: string; removed: number }>(
      `/agent/messages?${q.toString()}`,
      { method: "DELETE" },
    );
  },
  agentModels: () =>
    request<{ models: string[]; default_model: string }>("/agent/models"),
  agentChat: (
    body: {
      prompt: string;
      note_path?: string | null;
      session_id?: string | null;
      model_name?: string | null;
      image_paths?: string[];
      file_paths?: string[];
    },
    handlers: AgentChatHandlers,
    signal?: AbortSignal,
  ) => streamAgentChat(body, handlers, signal),
};

export type AgentToolEvent = {
  type: "text" | "tool" | "tool_result" | "info";
  tool?: string;
  input?: unknown;
  toolUseId?: string;
  data?: string;
};

export type AgentChatHandlers = {
  onSession?: (sessionId: string) => void;
  onToken?: (text: string) => void;
  onText?: (data: string) => void;
  onTool?: (event: AgentToolEvent) => void;
  onToolResult?: (event: AgentToolEvent) => void;
  onNoteUpdated?: (path: string) => void;
  onDone?: (
    result: string,
    sessionId?: string,
    toolEvents?: AgentToolEvent[],
  ) => void;
  onError?: (message: string) => void;
};

async function streamAgentChat(
  body: {
    prompt: string;
    note_path?: string | null;
    session_id?: string | null;
    model_name?: string | null;
    image_paths?: string[];
    file_paths?: string[];
  },
  handlers: AgentChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/agent/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      prompt: body.prompt,
      note_path: body.note_path || null,
      session_id: body.session_id || null,
      model_name: body.model_name || null,
      image_paths: body.image_paths || [],
      file_paths: body.file_paths || [],
    }),
    signal,
  });
  if (!res.ok) {
    const errBody = await readResponseBody(res);
    throw new Error(formatApiError(errBody, res.status));
  }
  if (!res.body) {
    throw new Error("Empty SSE response");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const lines = part.split("\n");
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;
        let payload: Record<string, unknown>;
        try {
          payload = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          continue;
        }
        const type = String(payload.type || "");
        if (type === "session" && typeof payload.session_id === "string") {
          handlers.onSession?.(payload.session_id);
        } else if (type === "token" && typeof payload.text === "string") {
          handlers.onToken?.(payload.text);
        } else if (type === "text" && typeof payload.data === "string") {
          handlers.onText?.(payload.data);
        } else if (type === "tool") {
          handlers.onTool?.({
            type: "tool",
            tool: typeof payload.tool === "string" ? payload.tool : undefined,
            input: payload.input,
            toolUseId:
              typeof payload.toolUseId === "string" ? payload.toolUseId : undefined,
          });
        } else if (type === "tool_result") {
          handlers.onToolResult?.({
            type: "tool_result",
            tool: typeof payload.tool === "string" ? payload.tool : undefined,
            toolUseId:
              typeof payload.toolUseId === "string" ? payload.toolUseId : undefined,
            data: typeof payload.data === "string" ? payload.data : undefined,
          });
        } else if (type === "note_updated" && typeof payload.path === "string") {
          handlers.onNoteUpdated?.(payload.path);
        } else if (type === "done") {
          finished = true;
          const toolEvents = Array.isArray(payload.tool_events)
            ? (payload.tool_events as AgentToolEvent[])
            : undefined;
          handlers.onDone?.(
            typeof payload.result === "string" ? payload.result : "",
            typeof payload.session_id === "string" ? payload.session_id : undefined,
            toolEvents,
          );
        } else if (type === "error") {
          finished = true;
          handlers.onError?.(
            typeof payload.error === "string" ? payload.error : "Agent error",
          );
        }
      }
    }
  }
  if (!finished && !signal?.aborted) {
    handlers.onError?.("연결이 종료되었습니다. 다시 시도해 주세요.");
  }
}
