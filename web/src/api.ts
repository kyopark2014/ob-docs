import type { FilePayload, GraphPayload, SearchHit, TreeNode } from "./types";

const BASE = "/vault/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    const err = new Error(
      typeof detail === "object" && detail && "detail" in detail
        ? JSON.stringify((detail as { detail: unknown }).detail)
        : `HTTP ${res.status}`,
    ) as Error & { status?: number; detail?: unknown };
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  return res.json() as Promise<T>;
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
  deletePath: (path: string) =>
    request<{ ok: boolean }>("/files/delete", {
      method: "POST",
      body: JSON.stringify({ path }),
    }),
  search: (q: string) =>
    request<{ query: string; results: SearchHit[] }>(
      `/search?q=${encodeURIComponent(q)}`,
    ),
  getGraph: () => request<GraphPayload>("/graph"),
  rebuildGraph: () => request<{ ok: boolean; notes: number; links: number }>("/graph/rebuild", { method: "POST" }),
};
