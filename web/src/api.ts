import type { FilePayload, GraphPayload, SearchHit, TreeNode } from "./types";

export type ShareEntry = {
  token: string;
  path: string;
  title: string;
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

const BASE = "/vault/api";

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
    request<{ user_id: string; agentic_work_url: string; authenticated: boolean }>(
      "/session",
    ),
  createLocalSession: (user_id = "local-dev") =>
    request<{ user_id: string; agentic_work_url: string }>("/session", {
      method: "POST",
      body: JSON.stringify({ user_id }),
    }),
  setSessionWithAccessToken: (access_token: string) =>
    request<{ user_id: string; agentic_work_url: string; authenticated: boolean }>(
      "/session",
      {
        method: "POST",
        body: JSON.stringify({ access_token }),
      },
    ),
  getPublicConfig: () =>
    request<{
      google_client_id: string;
      local_auth_bypass: boolean;
      agentic_work_url: string;
      project_name: string;
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
};
