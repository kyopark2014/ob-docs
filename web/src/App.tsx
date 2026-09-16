import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { FileTree } from "./components/FileTree";
import { GraphView } from "./components/GraphView";
import { MarkdownPreview } from "./components/MarkdownPreview";
import {
  BookIcon,
  EditIcon,
  FilesIcon,
  GraphIcon,
  PlusFileIcon,
  PlusFolderIcon,
  SearchIcon,
} from "./components/Icons";
import type {
  FilePayload,
  GraphPayload,
  OpenTab,
  PanelMode,
  SearchHit,
  TreeNode,
  ViewMode,
} from "./types";

export default function App() {
  const [ready, setReady] = useState(false);
  const [authError, setAuthError] = useState<{ login_url?: string } | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const [panel, setPanel] = useState<PanelMode>("files");
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [tabs, setTabs] = useState<OpenTab[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [file, setFile] = useState<FilePayload | null>(null);
  const [draft, setDraft] = useState("");
  const [viewMode, setViewMode] = useState<ViewMode>("preview");
  const [dirty, setDirty] = useState(false);
  const [searchQ, setSearchQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [graph, setGraph] = useState<GraphPayload | null>(null);
  const [saving, setSaving] = useState(false);

  const bootstrap = useCallback(async () => {
    try {
      const session = await api.getSession();
      setUserId(session.user_id);
      setAuthError(null);
    } catch (err) {
      const e = err as Error & { status?: number; detail?: unknown };
      if (e.status === 401) {
        try {
          const session = await api.createLocalSession();
          setUserId(session.user_id);
          setAuthError(null);
        } catch {
          const detail = e.detail as { detail?: { login_url?: string } } | undefined;
          setAuthError({ login_url: detail?.detail?.login_url });
          setReady(true);
          return;
        }
      } else {
        setAuthError({});
        setReady(true);
        return;
      }
    }
    const t = await api.getTree();
    setTree(t.children);
    setReady(true);
  }, []);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  const refreshTree = useCallback(async () => {
    const t = await api.getTree();
    setTree(t.children);
  }, []);

  const openFile = useCallback(
    async (path: string) => {
      if (dirty && activePath && draft !== file?.content) {
        const ok = window.confirm("저장하지 않은 변경이 있습니다. 계속할까요?");
        if (!ok) return;
      }
      const payload = await api.readFile(path);
      setFile(payload);
      setDraft(payload.content);
      setDirty(false);
      setActivePath(path);
      setTabs((prev) => {
        if (prev.some((t) => t.path === path)) return prev;
        return [...prev, { path, title: payload.title || path.split("/").pop() || path }];
      });
      setPanel("files");
      setViewMode("preview");
    },
    [activePath, dirty, draft, file?.content],
  );

  const closeTab = useCallback(
    (path: string) => {
      setTabs((prev) => {
        const next = prev.filter((t) => t.path !== path);
        if (activePath === path) {
          const fallback = next[next.length - 1];
          if (fallback) void openFile(fallback.path);
          else {
            setActivePath(null);
            setFile(null);
            setDraft("");
          }
        }
        return next;
      });
    },
    [activePath, openFile],
  );

  const save = useCallback(async () => {
    if (!activePath) return;
    setSaving(true);
    try {
      await api.writeFile(activePath, draft);
      const payload = await api.readFile(activePath);
      setFile(payload);
      setDraft(payload.content);
      setDirty(false);
      await refreshTree();
    } finally {
      setSaving(false);
    }
  }, [activePath, draft, refreshTree]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        void save();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  useEffect(() => {
    if (panel !== "search") return;
    const q = searchQ.trim();
    if (!q) {
      setHits([]);
      return;
    }
    const t = setTimeout(() => {
      void api.search(q).then((r) => setHits(r.results));
    }, 200);
    return () => clearTimeout(t);
  }, [panel, searchQ]);

  useEffect(() => {
    if (panel !== "graph") return;
    void api.getGraph().then(setGraph);
  }, [panel]);

  const onWikiClick = useCallback(
    async (target: string) => {
      // resolve by searching tree names
      const needle = target.replace(/\.md$/i, "").toLowerCase();
      const flatten = (nodes: TreeNode[]): TreeNode[] =>
        nodes.flatMap((n) => (n.type === "folder" ? flatten(n.children || []) : [n]));
      const all = flatten(tree);
      const hit = all.find((f) => {
        const stem = f.name.replace(/\.md$/i, "").toLowerCase();
        return stem === needle || f.path.toLowerCase().includes(needle);
      });
      if (hit) await openFile(hit.path);
      else window.alert(`노트를 찾을 수 없습니다: ${target}`);
    },
    [openFile, tree],
  );

  const createNote = useCallback(async () => {
    const name = window.prompt("새 노트 이름 (확장자 없이)", "Untitled");
    if (!name) return;
    const path = `00-Inbox/${name.replace(/\.md$/i, "")}.md`;
    const content = `---\ntitle: ${name}\ndate: ${new Date().toISOString().slice(0, 10)}\ntags: []\nstatus: draft\n---\n\n# ${name}\n\n`;
    await api.writeFile(path, content);
    await refreshTree();
    await openFile(path);
  }, [openFile, refreshTree]);

  const createFolder = useCallback(async () => {
    const name = window.prompt("새 폴더 경로", "notes/new-folder");
    if (!name) return;
    await api.mkdir(name);
    await refreshTree();
  }, [refreshTree]);

  const crumbs = useMemo(() => (activePath ? activePath.split("/") : []), [activePath]);

  if (!ready) {
    return <div className="empty-state">Loading vault…</div>;
  }

  if (authError && !userId) {
    return (
      <div className="login-gate">
        <div className="login-card">
          <h1>ob-docs</h1>
          <p>
            agentic-work에서 로그인한 뒤 이 페이지로 돌아오세요. 세션 쿠키를 공유합니다.
          </p>
          <a href={authError.login_url || "/"}>agentic-work로 이동</a>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <aside className="rail">
        <button
          type="button"
          className={`rail-btn${panel === "files" ? " active" : ""}`}
          title="Files"
          onClick={() => setPanel("files")}
        >
          <FilesIcon />
        </button>
        <button
          type="button"
          className={`rail-btn${panel === "search" ? " active" : ""}`}
          title="Search"
          onClick={() => setPanel("search")}
        >
          <SearchIcon />
        </button>
        <button
          type="button"
          className={`rail-btn${panel === "graph" ? " active" : ""}`}
          title="Graph"
          onClick={() => setPanel("graph")}
        >
          <GraphIcon />
        </button>
        <div className="rail-spacer" />
      </aside>

      <aside className="sidebar">
        {panel === "files" && (
          <>
            <div className="sidebar-header">
              <span>Files</span>
              <div className="sidebar-actions">
                <button type="button" className="icon-btn" title="New note" onClick={() => void createNote()}>
                  <PlusFileIcon />
                </button>
                <button type="button" className="icon-btn" title="New folder" onClick={() => void createFolder()}>
                  <PlusFolderIcon />
                </button>
              </div>
            </div>
            <div className="sidebar-body">
              <FileTree nodes={tree} activePath={activePath} onOpen={(p) => void openFile(p)} />
            </div>
          </>
        )}
        {panel === "search" && (
          <>
            <div className="sidebar-header">
              <span>Search</span>
            </div>
            <div className="search-box">
              <input
                value={searchQ}
                onChange={(e) => setSearchQ(e.target.value)}
                placeholder="Search vault…"
                autoFocus
              />
            </div>
            <div className="sidebar-body">
              {hits.map((h) => (
                <div key={h.path} className="search-hit" onClick={() => void openFile(h.path)}>
                  <div className="search-hit-title">{h.title}</div>
                  <div className="search-hit-path">{h.path}</div>
                  <div className="search-hit-snippet">{h.snippet}</div>
                </div>
              ))}
            </div>
          </>
        )}
        {panel === "graph" && (
          <>
            <div className="sidebar-header">
              <span>Graph</span>
            </div>
            <div className="sidebar-body" style={{ padding: 12, color: "var(--text-muted)", fontSize: 13 }}>
              위키링크 기반 관계 그래프입니다. 메인 영역에서 노드를 클릭하면 노트가 열립니다.
              <div style={{ marginTop: 10 }}>
                nodes: {graph?.nodes.length ?? "…"} · edges: {graph?.edges.length ?? "…"}
              </div>
            </div>
          </>
        )}
      </aside>

      <main className="main">
        {panel === "graph" ? (
          <GraphView
            graph={graph}
            onOpen={(p) => void openFile(p)}
            onRebuild={() => {
              void api.rebuildGraph().then(() => api.getGraph().then(setGraph));
            }}
          />
        ) : (
          <>
            <div className="tabs">
              {tabs.map((t) => (
                <div
                  key={t.path}
                  className={`tab${activePath === t.path ? " active" : ""}`}
                  onClick={() => void openFile(t.path)}
                >
                  <span className="tab-title">{t.title}</span>
                  <button
                    type="button"
                    className="tab-close"
                    onClick={(e) => {
                      e.stopPropagation();
                      closeTab(t.path);
                    }}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>

            {activePath && file ? (
              <>
                <div className="toolbar">
                  <div className="breadcrumb">
                    {crumbs.map((c, i) => (
                      <span key={`${c}-${i}`}>
                        {i > 0 ? " / " : ""}
                        {c.replace(/\.md$/i, "")}
                      </span>
                    ))}
                    {dirty ? " *" : ""}
                  </div>
                  <div className="toolbar-actions">
                    <button
                      type="button"
                      className="icon-btn"
                      title="Preview"
                      onClick={() => setViewMode("preview")}
                      style={{ color: viewMode === "preview" ? "var(--accent)" : undefined }}
                    >
                      <BookIcon />
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      title="Edit"
                      onClick={() => setViewMode("edit")}
                      style={{ color: viewMode === "edit" ? "var(--accent)" : undefined }}
                    >
                      <EditIcon />
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      title="Save"
                      onClick={() => void save()}
                      disabled={saving || !dirty}
                      style={{ opacity: dirty ? 1 : 0.4, fontSize: 11, width: "auto", padding: "0 8px" }}
                    >
                      {saving ? "Saving…" : "Save"}
                    </button>
                  </div>
                </div>
                <div className="content">
                  {viewMode === "edit" ? (
                    <div className="editor-pane">
                      <textarea
                        value={draft}
                        onChange={(e) => {
                          setDraft(e.target.value);
                          setDirty(e.target.value !== file.content);
                        }}
                        spellCheck={false}
                      />
                    </div>
                  ) : (
                    <MarkdownPreview content={draft} onWikiClick={(t) => void onWikiClick(t)} />
                  )}
                </div>
                <div className="status-bar">
                  <span>{file.backlinks.length} backlinks</span>
                  <span>{file.word_count} words</span>
                  <span>{file.char_count.toLocaleString()} characters</span>
                </div>
              </>
            ) : (
              <div className="empty-state">
                왼쪽에서 노트를 선택하거나 새 노트를 만드세요.
                <br />
                <span style={{ fontSize: 12 }}>Local-first · .md SoT · .vault settings</span>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
