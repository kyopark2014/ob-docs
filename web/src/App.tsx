import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent } from "react";
import { api } from "./api";
import { FileTree } from "./components/FileTree";
import {
  AlertDialog,
  ConfirmDialog,
  type ConfirmOptions,
} from "./components/ConfirmDialog";
import { ConfigDrawer } from "./components/ConfigDrawer";
import {
  FolderContextMenu,
  type ContextMenuState,
  type FileMenuAction,
  type FolderMenuAction,
} from "./components/FolderContextMenu";
import { GraphView } from "./components/GraphView";
import { MarkdownPreview } from "./components/MarkdownPreview";
import {
  AppearanceIcon,
  BookIcon,
  EditIcon,
  FilesIcon,
  GraphIcon,
  PlusFileIcon,
  PlusFolderIcon,
  SearchIcon,
  SettingsIcon,
} from "./components/Icons";
import { useTheme } from "./hooks/useTheme";
import type { Theme } from "./theme";
import type {
  FilePayload,
  GraphPayload,
  OpenTab,
  PanelMode,
  SearchHit,
  TreeNode,
  ViewMode,
} from "./types";

const THEME_OPTIONS = ["Light", "Dark"] as const;

function themeToLabel(theme: Theme): string {
  return theme === "light" ? "Light" : "Dark";
}

function labelToTheme(label: string): Theme {
  return label === "Light" ? "light" : "dark";
}

const SKIP_CONFIRM_PREFIX = "ob-docs:skip-confirm:";

function shouldSkipConfirm(key: string): boolean {
  try {
    return localStorage.getItem(`${SKIP_CONFIRM_PREFIX}${key}`) === "1";
  } catch {
    return false;
  }
}

function rememberSkipConfirm(key: string): void {
  try {
    localStorage.setItem(`${SKIP_CONFIRM_PREFIX}${key}`, "1");
  } catch {
    /* ignore */
  }
}

function noteTemplate(title: string): string {
  return `# ${title}\n\n`;
}

/** First ATX H1 in the note body (frontmatter skipped). */
function extractH1(md: string): string | null {
  let body = md;
  if (body.startsWith("---\n") || body.startsWith("---\r\n")) {
    const end = body.indexOf("\n---", 3);
    if (end >= 0) {
      const after = body.indexOf("\n", end + 4);
      body = after >= 0 ? body.slice(after + 1) : "";
    }
  }
  const m = body.match(/^#\s+(.+?)\s*$/m);
  return m ? m[1].trim() : null;
}

function sanitizeFilename(title: string): string {
  const cleaned = title
    .replace(/[\\/:*?"<>|#]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);
  return cleaned || "Untitled";
}

function uniqueNamedPath(
  parent: string,
  baseName: string,
  tree: TreeNode[],
  excludePath?: string,
): string {
  const children = (() => {
    if (!parent) return tree;
    const stack: TreeNode[] = [...tree];
    while (stack.length) {
      const n = stack.pop()!;
      if (n.path === parent && n.type === "folder") return n.children || [];
      if (n.children) stack.push(...n.children);
    }
    return [];
  })();

  const names = new Set(
    children.filter((c) => c.path !== excludePath).map((c) => c.name),
  );
  let name = `${baseName}.md`;
  let i = 1;
  while (names.has(name)) {
    i += 1;
    name = `${baseName} ${i}.md`;
  }
  return parent ? `${parent}/${name}` : name;
}

function uniqueNotePath(parent: string, tree: TreeNode[]): string {
  return uniqueNamedPath(parent, "Untitled", tree);
}

const LAST_NOTE_KEY = "ob-docs:last-note-path";

function flattenMarkdownPaths(nodes: TreeNode[]): string[] {
  const out: string[] = [];
  for (const n of nodes) {
    if (n.type === "file" && /\.md$/i.test(n.name)) out.push(n.path);
    if (n.children?.length) out.push(...flattenMarkdownPaths(n.children));
  }
  return out;
}

function readLastNotePath(): string | null {
  try {
    return localStorage.getItem(LAST_NOTE_KEY);
  } catch {
    return null;
  }
}

function writeLastNotePath(path: string): void {
  try {
    localStorage.setItem(LAST_NOTE_KEY, path);
  } catch {
    /* ignore */
  }
}

function noteParentDir(notePath: string): string {
  return notePath.includes("/") ? notePath.slice(0, notePath.lastIndexOf("/")) : "";
}

function flattenAllPaths(nodes: TreeNode[]): string[] {
  const out: string[] = [];
  for (const n of nodes) {
    out.push(n.path);
    if (n.children?.length) out.push(...flattenAllPaths(n.children));
  }
  return out;
}

function extFromImageMime(mime: string): string {
  const map: Record<string, string> = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
  };
  return map[mime.toLowerCase()] || "png";
}

function uniqueImagePath(parent: string, ext: string, tree: TreeNode[]): string {
  const existing = new Set(flattenAllPaths(tree));
  let path = "";
  do {
    const id =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID().replace(/-/g, "")
        : `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 10)}`;
    const name = `img_${id}.${ext}`;
    path = parent ? `${parent}/${name}` : name;
  } while (existing.has(path));
  return path;
}

export default function App() {
  const { theme, setTheme } = useTheme();
  const [ready, setReady] = useState(false);
  const [authError, setAuthError] = useState<{ login_url?: string } | null>(null);
  const [userId, setUserId] = useState<string | null>(null);
  const [panel, setPanel] = useState<PanelMode>("files");
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [tabs, setTabs] = useState<OpenTab[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [file, setFile] = useState<FilePayload | null>(null);
  const [draft, setDraft] = useState("");
  const [viewMode, setViewMode] = useState<ViewMode>("preview");
  const [dirty, setDirty] = useState(false);
  const [searchQ, setSearchQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [graph, setGraph] = useState<GraphPayload | null>(null);
  const [saving, setSaving] = useState(false);
  const [draftFolder, setDraftFolder] = useState<{ parentPath: string } | null>(null);
  const [renamingPath, setRenamingPath] = useState<string | null>(null);
  const [ctxMenu, setCtxMenu] = useState<ContextMenuState | null>(null);
  const [confirmState, setConfirmState] = useState<{
    options: ConfirmOptions;
    resolve: (ok: boolean) => void;
  } | null>(null);
  const [alertState, setAlertState] = useState<{
    title?: string;
    message: string;
    resolve: () => void;
  } | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [appearanceOpen, setAppearanceOpen] = useState(false);
  const [settingsFlyoutPos, setSettingsFlyoutPos] = useState<{ left: number; bottom: number } | null>(
    null,
  );
  const [pastingImage, setPastingImage] = useState(false);
  const settingsBtnRef = useRef<HTMLButtonElement>(null);
  const appearanceBtnRef = useRef<HTMLButtonElement>(null);
  const settingsFlyoutRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<HTMLTextAreaElement | null>(null);
  const renameInFlight = useRef(false);
  const draftRef = useRef(draft);
  const activePathRef = useRef(activePath);
  const treeRef = useRef(tree);
  const didRestoreNote = useRef(false);
  draftRef.current = draft;
  activePathRef.current = activePath;
  treeRef.current = tree;

  const syncFilenameToH1 = useCallback(
    async (path: string, content: string, currentTree: TreeNode[]): Promise<string> => {
      const title = extractH1(content);
      if (!title) return path;
      const safe = sanitizeFilename(title);
      if (!safe) return path;
      const parts = path.split("/");
      const parent = parts.slice(0, -1).join("/");
      const currentStem = parts[parts.length - 1]?.replace(/\.md$/i, "") || "";
      if (safe === currentStem) {
        await api.writeFile(path, content);
        return path;
      }
      if (renameInFlight.current) return path;
      renameInFlight.current = true;
      try {
        await api.writeFile(path, content);
        const dest = uniqueNamedPath(parent, safe, currentTree, path);
        if (dest === path) return path;
        await api.rename(path, dest);
        return dest;
      } finally {
        renameInFlight.current = false;
      }
    },
    [],
  );

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

  useEffect(() => {
    if (!settingsOpen) {
      setAppearanceOpen(false);
      setSettingsFlyoutPos(null);
      return;
    }

    function updatePos() {
      const btn = settingsBtnRef.current;
      if (!btn) return;
      const rect = btn.getBoundingClientRect();
      setSettingsFlyoutPos({
        left: rect.right + 10,
        bottom: window.innerHeight - rect.bottom,
      });
    }

    updatePos();
    window.addEventListener("resize", updatePos);

    function onPointerDown(e: MouseEvent) {
      const target = e.target as Node;
      if (settingsBtnRef.current?.contains(target)) return;
      if (settingsFlyoutRef.current?.contains(target)) return;
      if ((target as Element).closest?.(".config-popover")) return;
      setSettingsOpen(false);
    }

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setSettingsOpen(false);
    }

    document.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", updatePos);
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [settingsOpen]);

  const refreshTree = useCallback(async () => {
    const t = await api.getTree();
    setTree(t.children);
  }, []);

  const askConfirm = useCallback((options: ConfirmOptions) => {
    if (options.dontAskAgainKey && shouldSkipConfirm(options.dontAskAgainKey)) {
      return Promise.resolve(true);
    }
    return new Promise<boolean>((resolve) => {
      setConfirmState({ options, resolve });
    });
  }, []);

  const showAlert = useCallback((message: string, title = "Notice") => {
    return new Promise<void>((resolve) => {
      setAlertState({ title, message, resolve });
    });
  }, []);

  const persistNote = useCallback(
    async (path: string, content: string): Promise<{ finalPath: string; content: string }> => {
      let body = content;
      if (!extractH1(body)) {
        const stem = path.split("/").pop()?.replace(/\.md$/i, "") || "Untitled";
        body = `# ${stem}\n\n${body.replace(/^\s+/, "")}`;
      }
      const finalPath = await syncFilenameToH1(path, body, treeRef.current);
      const safe = sanitizeFilename(extractH1(body) || "Untitled");
      setTabs((prev) =>
        prev.map((t) =>
          t.path === path || t.path === finalPath ? { ...t, path: finalPath, title: safe } : t,
        ),
      );
      await refreshTree();
      return { finalPath, content: body };
    },
    [refreshTree, syncFilenameToH1],
  );

  const openFile = useCallback(
    async (path: string) => {
      if (activePath && path !== activePath && dirty && draft !== file?.content) {
        try {
          await persistNote(activePath, draft);
        } catch (err) {
          void showAlert(err instanceof Error ? err.message : String(err), "Save failed");
          return;
        }
      }
      const payload = await api.readFile(path);
      setFile(payload);
      setDraft(payload.content);
      setDirty(false);
      setActivePath(path);
      writeLastNotePath(path);
      setTabs((prev) => {
        if (prev.some((t) => t.path === path)) return prev;
        return [...prev, { path, title: payload.title || path.split("/").pop() || path }];
      });
      setPanel("files");
      setViewMode("preview");
    },
    [activePath, dirty, draft, file?.content, persistNote, showAlert],
  );

  // On refresh: restore last note, else open first markdown file
  useEffect(() => {
    if (!ready || didRestoreNote.current || authError) return;
    if (activePath) {
      didRestoreNote.current = true;
      return;
    }
    const files = flattenMarkdownPaths(tree);
    if (!files.length) {
      didRestoreNote.current = true;
      return;
    }
    const last = readLastNotePath();
    const toOpen = last && files.includes(last) ? last : files[0];
    didRestoreNote.current = true;
    void openFile(toOpen);
  }, [ready, tree, activePath, authError, openFile]);

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
      const { finalPath } = await persistNote(activePath, draft);
      if (finalPath !== activePath) {
        setActivePath(finalPath);
        writeLastNotePath(finalPath);
      }
      const payload = await api.readFile(finalPath);
      setFile(payload);
      setDraft(payload.content);
      setDirty(false);
      setViewMode("preview");
    } catch (err) {
      void showAlert(err instanceof Error ? err.message : String(err), "Save failed");
    } finally {
      setSaving(false);
    }
  }, [activePath, draft, persistNote, showAlert]);

  // Live tab label + debounced file rename when H1 changes
  useEffect(() => {
    if (!activePath) return;
    const title = extractH1(draft);
    if (!title) return;
    const label = sanitizeFilename(title);
    setTabs((prev) =>
      prev.map((t) => (t.path === activePath ? { ...t, title: label } : t)),
    );

    const currentStem =
      activePath.split("/").pop()?.replace(/\.md$/i, "") || "";
    if (label === currentStem) return;

    const pathWhenScheduled = activePath;
    const timer = window.setTimeout(() => {
      void (async () => {
        const path = activePathRef.current;
        const content = draftRef.current;
        if (!path) return;
        const liveTitle = extractH1(content);
        if (!liveTitle) return;
        const liveLabel = sanitizeFilename(liveTitle);
        const stemNow = path.split("/").pop()?.replace(/\.md$/i, "") || "";
        if (liveLabel === stemNow) return;
        try {
          const dest = await syncFilenameToH1(path, content, treeRef.current);
          setActivePath((prev) =>
            prev === path || prev === pathWhenScheduled ? dest : prev,
          );
          writeLastNotePath(dest);
          setTabs((prev) =>
            prev.map((t) =>
              t.path === path || t.path === pathWhenScheduled
                ? { ...t, path: dest, title: liveLabel }
                : t,
            ),
          );
          setFile((prev) =>
            prev && (prev.path === path || prev.path === pathWhenScheduled)
              ? { ...prev, path: dest, title: liveLabel, content }
              : prev,
          );
          if (draftRef.current === content) setDirty(false);
          await refreshTree();
        } catch {
          /* ignore transient rename errors while typing */
        }
      })();
    }, 400);
    return () => window.clearTimeout(timer);
  }, [activePath, draft, refreshTree, syncFilenameToH1]);

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
      const needle = target.replace(/\.md$/i, "").toLowerCase();
      const flatten = (nodes: TreeNode[]): TreeNode[] =>
        nodes.flatMap((n) => (n.type === "folder" ? flatten(n.children || []) : [n]));
      const all = flatten(tree);
      const hit = all.find((f) => {
        const stem = f.name.replace(/\.md$/i, "").toLowerCase();
        return stem === needle || f.path.toLowerCase().includes(needle);
      });
      if (hit) await openFile(hit.path);
      else void showAlert(`노트를 찾을 수 없습니다: ${target}`, "Not found");
    },
    [openFile, showAlert, tree],
  );

  const onEditorPaste = useCallback(
    async (e: ClipboardEvent<HTMLTextAreaElement>) => {
      if (!activePath || pastingImage) return;
      const items = Array.from(e.clipboardData?.items || []);
      const imageItem = items.find((it) => it.type.startsWith("image/"));
      if (!imageItem) return;

      const blob = imageItem.getAsFile();
      if (!blob) return;
      e.preventDefault();

      const ta = e.currentTarget;
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const parent = noteParentDir(activePath);
      const ext = extFromImageMime(blob.type || "image/png");
      const vaultPath = uniqueImagePath(parent, ext, treeRef.current);
      const fileName = vaultPath.split("/").pop() || `image.${ext}`;
      const md = `![image](${fileName})`;

      setPastingImage(true);
      try {
        await api.uploadFile(vaultPath, blob, fileName);
        const next = `${draftRef.current.slice(0, start)}${md}${draftRef.current.slice(end)}`;
        setDraft(next);
        setDirty(true);
        await refreshTree();
        requestAnimationFrame(() => {
          const el = editorRef.current;
          if (!el) return;
          const caret = start + md.length;
          el.focus();
          el.setSelectionRange(caret, caret);
        });
      } catch (err) {
        void showAlert(err instanceof Error ? err.message : String(err), "Image paste failed");
      } finally {
        setPastingImage(false);
      }
    },
    [activePath, pastingImage, refreshTree, showAlert],
  );

  const draftParentPath = useMemo(() => {
    if (selectedFolder) return selectedFolder;
    if (!activePath) return "";
    const parts = activePath.split("/");
    if (parts.length <= 1) return "";
    return parts.slice(0, -1).join("/");
  }, [activePath, selectedFolder]);

  const createNoteIn = useCallback(
    async (parent: string) => {
      const path = uniqueNotePath(parent, tree);
      await api.writeFile(path, noteTemplate("Untitled"));
      await refreshTree();
      await openFile(path);
      setViewMode("edit");
    },
    [openFile, refreshTree, tree],
  );

  const createNote = useCallback(async () => {
    await createNoteIn(draftParentPath || "00-Inbox");
  }, [createNoteIn, draftParentPath]);

  const startCreateFolder = useCallback(
    (parentPath?: string) => {
      setPanel("files");
      setCtxMenu(null);
      setRenamingPath(null);
      setDraftFolder({ parentPath: parentPath ?? draftParentPath });
    },
    [draftParentPath],
  );

  const confirmCreateFolder = useCallback(
    async (name: string) => {
      const parent = draftFolder?.parentPath ?? "";
      const safe = name.replace(/[\\/]/g, "").trim();
      setDraftFolder(null);
      if (!safe) return;
      const path = parent ? `${parent}/${safe}` : safe;
      try {
        await api.mkdir(path);
        setSelectedFolder(path);
        await refreshTree();
      } catch (err) {
        void showAlert(err instanceof Error ? err.message : String(err));
      }
    },
    [draftFolder, refreshTree, showAlert],
  );

  const cancelCreateFolder = useCallback(() => {
    setDraftFolder(null);
  }, []);

  const confirmRename = useCallback(
    async (path: string, name: string) => {
      setRenamingPath(null);
      let safe = name.replace(/[\\/]/g, "").trim();
      if (!safe) return;
      const isFile = /\.[a-z0-9]+$/i.test(path.split("/").pop() || "");
      if (isFile && path.toLowerCase().endsWith(".md") && !safe.toLowerCase().endsWith(".md")) {
        safe = `${safe}.md`;
      }
      const parts = path.split("/");
      const parent = parts.slice(0, -1).join("/");
      const to = parent ? `${parent}/${safe}` : safe;
      if (to === path) return;
      try {
        await api.rename(path, to);
        if (selectedFolder === path) setSelectedFolder(to);
        if (activePath === path) {
          setActivePath(to);
          writeLastNotePath(to);
          setTabs((prev) =>
            prev.map((t) =>
              t.path === path
                ? { ...t, path: to, title: safe.replace(/\.md$/i, "") }
                : t,
            ),
          );
          try {
            const payload = await api.readFile(to);
            setFile(payload);
            setDraft(payload.content);
            setDirty(false);
          } catch {
            /* ignore */
          }
        } else {
          setTabs((prev) =>
            prev.map((t) =>
              t.path === path
                ? { ...t, path: to, title: safe.replace(/\.md$/i, "") }
                : t,
            ),
          );
        }
        await refreshTree();
      } catch (err) {
        void showAlert(err instanceof Error ? err.message : String(err));
      }
    },
    [activePath, refreshTree, selectedFolder, showAlert],
  );

  const onFileMenuAction = useCallback(
    async (action: FileMenuAction, path: string) => {
      if (action === "open-tab") {
        await openFile(path);
        return;
      }
      if (action === "duplicate") {
        try {
          const res = await api.duplicate(path);
          await refreshTree();
          await openFile(res.to);
        } catch (err) {
          void showAlert(err instanceof Error ? err.message : String(err));
        }
        return;
      }
      if (action === "rename") {
        setDraftFolder(null);
        setRenamingPath(path);
        return;
      }
      if (action === "delete") {
        const label = path.split("/").pop() || path;
        const ok = await askConfirm({
          title: "Delete file",
          message: `Are you sure you want to delete “${label}”?`,
          detail: "This cannot be undone.",
          confirmLabel: "Delete",
          cancelLabel: "Cancel",
          danger: true,
          dontAskAgainKey: "delete-file",
        });
        if (!ok) return;
        try {
          await api.deletePath(path);
          setTabs((prev) => prev.filter((t) => t.path !== path));
          if (activePath === path) {
            setActivePath(null);
            setFile(null);
            setDraft("");
          }
          await refreshTree();
        } catch (err) {
          void showAlert(err instanceof Error ? err.message : String(err));
        }
      }
    },
    [activePath, askConfirm, openFile, refreshTree, showAlert],
  );

  const onFolderMenuAction = useCallback(
    async (action: FolderMenuAction, path: string) => {
      setSelectedFolder(path);
      if (action === "new-note") {
        await createNoteIn(path);
        return;
      }
      if (action === "new-folder") {
        startCreateFolder(path);
        return;
      }
      if (action === "duplicate") {
        try {
          const res = await api.duplicate(path);
          setSelectedFolder(res.to);
          await refreshTree();
        } catch (err) {
          void showAlert(err instanceof Error ? err.message : String(err));
        }
        return;
      }
      if (action === "rename") {
        setDraftFolder(null);
        setRenamingPath(path);
        return;
      }
      if (action === "delete") {
        const label = path.split("/").pop() || path;
        const ok = await askConfirm({
          title: "Delete folder",
          message: `Are you sure you want to delete “${label}”?`,
          detail: "All notes inside this folder will be permanently deleted.",
          confirmLabel: "Delete",
          cancelLabel: "Cancel",
          danger: true,
          dontAskAgainKey: "delete-folder",
        });
        if (!ok) return;
        try {
          await api.deletePath(path);
          if (selectedFolder === path) setSelectedFolder(null);
          if (activePath?.startsWith(path + "/")) {
            setActivePath(null);
            setFile(null);
            setDraft("");
          }
          setTabs((prev) => prev.filter((t) => !t.path.startsWith(path + "/") && t.path !== path));
          await refreshTree();
        } catch (err) {
          void showAlert(err instanceof Error ? err.message : String(err));
        }
      }
    },
    [activePath, askConfirm, createNoteIn, refreshTree, selectedFolder, showAlert, startCreateFolder],
  );

  const crumbs = useMemo(() => {
    if (!activePath) return [];
    const parts = activePath.split("/");
    const h1 = extractH1(draft);
    const last = h1
      ? sanitizeFilename(h1)
      : parts[parts.length - 1].replace(/\.md$/i, "");
    return [...parts.slice(0, -1), last];
  }, [activePath, draft]);

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
      {ctxMenu && (
        <FolderContextMenu
          menu={ctxMenu}
          onFolderAction={(action, path) => void onFolderMenuAction(action, path)}
          onFileAction={(action, path) => void onFileMenuAction(action, path)}
          onClose={() => setCtxMenu(null)}
        />
      )}
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
        <button
          ref={settingsBtnRef}
          type="button"
          className={`rail-btn${settingsOpen ? " active" : ""}`}
          title="Settings"
          aria-label="Settings"
          aria-expanded={settingsOpen}
          onClick={() => setSettingsOpen((v) => !v)}
        >
          <SettingsIcon />
        </button>
      </aside>

      {settingsOpen && settingsFlyoutPos && (
        <div
          ref={settingsFlyoutRef}
          className="rail-settings-flyout"
          style={{ left: settingsFlyoutPos.left, bottom: settingsFlyoutPos.bottom }}
        >
          <button
            ref={appearanceBtnRef}
            type="button"
            className={`rail-settings-btn${appearanceOpen ? " is-active" : ""}`}
            aria-expanded={appearanceOpen}
            aria-haspopup="dialog"
            onClick={() => setAppearanceOpen((v) => !v)}
          >
            <AppearanceIcon />
            <span>Appearance ({themeToLabel(theme)})</span>
          </button>
        </div>
      )}
      {appearanceOpen && (
        <ConfigDrawer
          title="Appearance"
          options={[...THEME_OPTIONS]}
          selected={[themeToLabel(theme)]}
          mode="single"
          anchorEl={appearanceBtnRef.current}
          onChange={(next) => {
            if (next[0]) setTheme(labelToTheme(next[0]));
          }}
          onClose={() => setAppearanceOpen(false)}
        />
      )}

      <aside className="sidebar">
        {panel === "files" && (
          <>
            <div className="sidebar-header">
              <div className="sidebar-actions">
                <button
                  type="button"
                  className="icon-btn"
                  data-tooltip="New note"
                  aria-label="New note"
                  onClick={() => void createNote()}
                >
                  <PlusFileIcon />
                </button>
                <button
                  type="button"
                  className={`icon-btn${draftFolder ? " active" : ""}`}
                  data-tooltip="New folder"
                  aria-label="New folder"
                  onClick={() => startCreateFolder()}
                >
                  <PlusFolderIcon />
                </button>
              </div>
            </div>
            <div className="sidebar-body">
              <FileTree
                nodes={tree}
                activePath={activePath}
                selectedFolder={selectedFolder}
                onOpen={(p) => void openFile(p)}
                onSelectFolder={setSelectedFolder}
                onFolderContextMenu={(path, x, y) =>
                  setCtxMenu({ kind: "folder", path, x, y })
                }
                onFileContextMenu={(path, x, y) =>
                  setCtxMenu({ kind: "file", path, x, y })
                }
                draftFolder={draftFolder}
                onDraftConfirm={(name) => void confirmCreateFolder(name)}
                onDraftCancel={cancelCreateFolder}
                renamingPath={renamingPath}
                onRenameConfirm={(path, name) => void confirmRename(path, name)}
                onRenameCancel={() => setRenamingPath(null)}
              />
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
                          const el = e.target;
                          setDraft(el.value);
                          setDirty(el.value !== file.content);
                          el.style.height = "auto";
                          el.style.height = `${Math.max(el.scrollHeight, 320)}px`;
                        }}
                        onPaste={(e) => void onEditorPaste(e)}
                        onFocus={(e) => {
                          const el = e.target;
                          el.style.height = "auto";
                          el.style.height = `${Math.max(el.scrollHeight, 320)}px`;
                        }}
                        ref={(el) => {
                          editorRef.current = el;
                          if (!el) return;
                          el.style.height = "auto";
                          el.style.height = `${Math.max(el.scrollHeight, 320)}px`;
                        }}
                        spellCheck={false}
                      />
                    </div>
                  ) : (
                    <MarkdownPreview
                      content={draft}
                      notePath={activePath}
                      onWikiClick={(t) => void onWikiClick(t)}
                    />
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

      <ConfirmDialog
        open={!!confirmState}
        options={confirmState?.options ?? null}
        onCancel={() => {
          confirmState?.resolve(false);
          setConfirmState(null);
        }}
        onConfirm={(dontAskAgain) => {
          if (dontAskAgain && confirmState?.options.dontAskAgainKey) {
            rememberSkipConfirm(confirmState.options.dontAskAgainKey);
          }
          confirmState?.resolve(true);
          setConfirmState(null);
        }}
      />
      <AlertDialog
        open={!!alertState}
        title={alertState?.title}
        message={alertState?.message ?? ""}
        onClose={() => {
          alertState?.resolve();
          setAlertState(null);
        }}
      />
    </div>
  );
}
