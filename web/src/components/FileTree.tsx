import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type DragEvent,
} from "react";
import type { TreeNode } from "../types";
import { ChevronIcon, PinIcon } from "./Icons";

const DND_TYPE = "application/x-ob-docs-path";
const DND_PREFIX = "ob-docs-move|";
/** "" = vault root drop target */
type DropTarget = string | null;

const DropHighlightCtx = createContext<{
  target: DropTarget;
  setTarget: (t: DropTarget) => void;
  clear: () => void;
}>({ target: null, setTarget: () => {}, clear: () => {} });

export type DraftFolder = {
  parentPath: string; // "" = vault root
};

type DragPayload = {
  path: string;
  kind: "file" | "folder";
};

type Props = {
  nodes: TreeNode[];
  activePath: string | null;
  selectedFolder?: string | null;
  onOpen: (path: string) => void;
  onSelectFolder?: (path: string) => void;
  onFolderContextMenu?: (path: string, x: number, y: number) => void;
  onFileContextMenu?: (path: string, x: number, y: number) => void;
  onMove?: (fromPath: string, toParentPath: string) => void;
  onUploadFiles?: (parentPath: string, files: File[]) => void;
  draftFolder?: DraftFolder | null;
  onDraftConfirm?: (name: string) => void;
  onDraftCancel?: () => void;
  renamingPath?: string | null;
  onRenameConfirm?: (path: string, name: string) => void;
  onRenameCancel?: () => void;
  depth?: number;
  forceOpenPaths?: Set<string>;
  pinnedPaths?: Set<string>;
  hidePinBadge?: boolean;
};

function serializeDrag(payload: DragPayload): string {
  return `${DND_PREFIX}${payload.kind}|${payload.path}`;
}

function parseDrag(e: DragEvent): DragPayload | null {
  try {
    const raw = e.dataTransfer.getData(DND_TYPE) || e.dataTransfer.getData("text/plain");
    if (!raw) return null;
    if (raw.startsWith(DND_PREFIX)) {
      const rest = raw.slice(DND_PREFIX.length);
      const bar = rest.indexOf("|");
      if (bar < 0) return null;
      const kind = rest.slice(0, bar);
      const path = rest.slice(bar + 1);
      if (!path || (kind !== "file" && kind !== "folder")) return null;
      return { path, kind };
    }
    const data = JSON.parse(raw) as DragPayload;
    if (!data?.path || (data.kind !== "file" && data.kind !== "folder")) return null;
    return data;
  } catch {
    return null;
  }
}

function hasExternalFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer.types || []).includes("Files");
}

function isInternalMoveDrag(e: DragEvent): boolean {
  const types = Array.from(e.dataTransfer.types || []);
  return types.includes(DND_TYPE);
}

function parentDir(path: string): string {
  return path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
}

function canDropOnFolder(fromPath: string, fromKind: "file" | "folder", toFolder: string): boolean {
  if (fromPath === toFolder) return false;
  if (fromKind === "folder" && (toFolder === fromPath || toFolder.startsWith(fromPath + "/"))) {
    return false;
  }
  if (parentDir(fromPath) === toFolder) return false;
  return true;
}

/** Prefer vault move over OS-file upload when both are present. */
function acceptDrop(
  e: DragEvent,
  folderPath: string,
  onMove?: (fromPath: string, toParentPath: string) => void,
  onUploadFiles?: (parentPath: string, files: File[]) => void,
): boolean {
  const data = parseDrag(e);
  if (data && onMove) {
    if (!canDropOnFolder(data.path, data.kind, folderPath)) return false;
    onMove(data.path, folderPath);
    return true;
  }
  if (onUploadFiles && e.dataTransfer.files?.length) {
    onUploadFiles(folderPath, Array.from(e.dataTransfer.files));
    return true;
  }
  return false;
}

export function FileTree(props: Props) {
  const { depth = 0 } = props;

  if (depth === 0) {
    return <FileTreeRoot {...props} />;
  }
  return <FileTreeBranch {...props} />;
}

function FileTreeRoot(props: Props) {
  const [target, setTarget] = useState<DropTarget>(null);
  const clear = useCallback(() => setTarget(null), []);

  useEffect(() => {
    function onDragEnd() {
      clear();
    }
    window.addEventListener("dragend", onDragEnd);
    return () => {
      window.removeEventListener("dragend", onDragEnd);
    };
  }, [clear]);

  return (
    <DropHighlightCtx.Provider value={{ target, setTarget, clear }}>
      <FileTreeBranch {...props} depth={0} />
    </DropHighlightCtx.Provider>
  );
}

function FileTreeBranch(props: Props) {
  const {
    nodes,
    draftFolder = null,
    onDraftConfirm,
    onDraftCancel,
    onMove,
    onUploadFiles,
    depth = 0,
  } = props;
  const { target, setTarget, clear } = useContext(DropHighlightCtx);

  const showDraftHere =
    !!draftFolder && depth === 0 && draftFolder.parentPath === "";

  return (
    <div
      className={
        depth === 0
          ? `file-tree${target === "" ? " drop-target" : ""}`
          : undefined
      }
      onDragOver={
        depth === 0 && (onMove || onUploadFiles)
          ? (e) => {
              const el = e.target as HTMLElement;
              const onChildItem = !!el.closest?.(".tree-item");
              if (isInternalMoveDrag(e) || hasExternalFiles(e) || e.dataTransfer.types.includes("text/plain")) {
                e.preventDefault();
                e.dataTransfer.dropEffect =
                  isInternalMoveDrag(e) || !hasExternalFiles(e) ? "move" : "copy";
              }
              if (!onChildItem) setTarget("");
            }
          : undefined
      }
      onDragLeave={
        depth === 0
          ? (e) => {
              if (e.currentTarget.contains(e.relatedTarget as Node)) return;
              clear();
            }
          : undefined
      }
      onDrop={
        depth === 0 && (onMove || onUploadFiles)
          ? (e) => {
              e.preventDefault();
              clear();
              const el = e.target as HTMLElement;
              if (el.closest?.(".tree-item")) return;
              acceptDrop(e, "", onMove, onUploadFiles);
            }
          : undefined
      }
    >
      {nodes.map((node) => (
        <TreeRow
          key={node.path}
          node={node}
          {...props}
          draftFolder={draftFolder}
          depth={depth}
        />
      ))}
      {showDraftHere && onDraftConfirm && onDraftCancel && (
        <DraftFolderRow
          depth={depth}
          onConfirm={onDraftConfirm}
          onCancel={onDraftCancel}
        />
      )}
    </div>
  );
}

function TreeRow({
  node,
  activePath,
  selectedFolder = null,
  onOpen,
  onSelectFolder,
  onFolderContextMenu,
  onFileContextMenu,
  onMove,
  onUploadFiles,
  draftFolder = null,
  onDraftConfirm,
  onDraftCancel,
  renamingPath = null,
  onRenameConfirm,
  onRenameCancel,
  depth = 0,
  forceOpenPaths,
  pinnedPaths,
  hidePinBadge = false,
}: Props & { node: TreeNode }) {
  const { target, setTarget, clear } = useContext(DropHighlightCtx);
  const suppressClick = useRef(false);
  const shouldForce =
    forceOpenPaths?.has(node.path) ||
    draftFolder?.parentPath === node.path ||
    (draftFolder?.parentPath.startsWith(node.path + "/") ?? false) ||
    (activePath?.startsWith(node.path + "/") ?? false) ||
    (renamingPath?.startsWith(node.path + "/") ?? false);

  const [open, setOpen] = useState(
    depth < 1 ||
      node.path.startsWith("00-Inbox") ||
      node.path === "notes" ||
      !!shouldForce,
  );

  useEffect(() => {
    if (shouldForce) setOpen(true);
  }, [shouldForce]);

  if (node.type === "folder") {
    const showDraftInside =
      !!draftFolder &&
      draftFolder.parentPath === node.path &&
      open &&
      onDraftConfirm &&
      onDraftCancel;

    const isSelected = selectedFolder === node.path;
    const isRenaming = renamingPath === node.path;
    const isDropOver = target === node.path;

    return (
      <div className="tree-folder">
        {isRenaming && onRenameConfirm && onRenameCancel ? (
          <RenameRow
            depth={depth}
            initial={node.name}
            isFolder
            onConfirm={(name) => onRenameConfirm(node.path, name)}
            onCancel={onRenameCancel}
          />
        ) : (
          <div
            className={`tree-item${isSelected ? " selected" : ""}${isDropOver ? " drop-over" : ""}`}
            style={{ paddingLeft: 10 + depth * 14 }}
            draggable={!!onMove}
            onDragStart={(e) => {
              if (!onMove) return;
              suppressClick.current = true;
              const payload: DragPayload = { path: node.path, kind: "folder" };
              const serialized = serializeDrag(payload);
              e.dataTransfer.setData(DND_TYPE, serialized);
              e.dataTransfer.setData("text/plain", serialized);
              e.dataTransfer.effectAllowed = "move";
              clear();
            }}
            onDragEnd={() => {
              clear();
              window.setTimeout(() => {
                suppressClick.current = false;
              }, 0);
            }}
            onDragOver={(e) => {
              if (!onMove && !onUploadFiles) return;
              e.preventDefault();
              e.stopPropagation();
              e.dataTransfer.dropEffect =
                isInternalMoveDrag(e) || !hasExternalFiles(e) ? "move" : "copy";
              setTarget(node.path);
            }}
            onDragLeave={(e) => {
              if (e.currentTarget.contains(e.relatedTarget as Node)) return;
              if (target === node.path) clear();
            }}
            onDrop={(e) => {
              e.preventDefault();
              e.stopPropagation();
              clear();
              if (acceptDrop(e, node.path, onMove, onUploadFiles)) {
                setOpen(true);
              }
            }}
            onClick={() => {
              if (suppressClick.current) return;
              onSelectFolder?.(node.path);
              setOpen((v) => !v);
            }}
            onContextMenu={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onSelectFolder?.(node.path);
              onFolderContextMenu?.(node.path, e.clientX, e.clientY);
            }}
          >
            <span className="tree-chevron">
              <ChevronIcon open={open} />
            </span>
            {!hidePinBadge && pinnedPaths?.has(node.path) && (
              <span className="tree-pin-icon" aria-hidden="true">
                <PinIcon />
              </span>
            )}
            <span className="tree-name">{node.name}</span>
          </div>
        )}
        {open && (
          <div
            className={`tree-children${isDropOver ? " drop-over" : ""}`}
            onDragOver={
              onMove || onUploadFiles
                ? (e) => {
                    const el = e.target as HTMLElement;
                    if (el.closest?.(".tree-item")) return;
                    e.preventDefault();
                    e.stopPropagation();
                    e.dataTransfer.dropEffect =
                      isInternalMoveDrag(e) || !hasExternalFiles(e) ? "move" : "copy";
                    setTarget(node.path);
                  }
                : undefined
            }
            onDrop={
              onMove || onUploadFiles
                ? (e) => {
                    const el = e.target as HTMLElement;
                    if (el.closest?.(".tree-item")) return;
                    e.preventDefault();
                    e.stopPropagation();
                    clear();
                    acceptDrop(e, node.path, onMove, onUploadFiles);
                  }
                : undefined
            }
          >
            {node.children && (
              <FileTree
                nodes={node.children}
                activePath={activePath}
                selectedFolder={selectedFolder}
                onOpen={onOpen}
                onSelectFolder={onSelectFolder}
                onFolderContextMenu={onFolderContextMenu}
                onFileContextMenu={onFileContextMenu}
                onMove={onMove}
                onUploadFiles={onUploadFiles}
                draftFolder={draftFolder}
                onDraftConfirm={onDraftConfirm}
                onDraftCancel={onDraftCancel}
                renamingPath={renamingPath}
                onRenameConfirm={onRenameConfirm}
                onRenameCancel={onRenameCancel}
                depth={depth + 1}
                forceOpenPaths={forceOpenPaths}
                pinnedPaths={pinnedPaths}
                hidePinBadge={hidePinBadge}
              />
            )}
            {showDraftInside && (
              <DraftFolderRow
                depth={depth + 1}
                onConfirm={onDraftConfirm!}
                onCancel={onDraftCancel!}
              />
            )}
          </div>
        )}
      </div>
    );
  }

  const isRenaming = renamingPath === node.path;
  const displayName = node.name.replace(/\.md$/i, "");

  if (isRenaming && onRenameConfirm && onRenameCancel) {
    return (
      <RenameRow
        depth={depth}
        initial={displayName}
        onConfirm={(name) => onRenameConfirm(node.path, name)}
        onCancel={onRenameCancel}
      />
    );
  }

  return (
    <div
      className={`tree-item${activePath === node.path ? " active" : ""}`}
      style={{ paddingLeft: 10 + depth * 14 + 14 }}
      draggable={!!onMove}
      onDragStart={(e) => {
        if (!onMove) return;
        suppressClick.current = true;
        const payload: DragPayload = { path: node.path, kind: "file" };
        const serialized = serializeDrag(payload);
        e.dataTransfer.setData(DND_TYPE, serialized);
        e.dataTransfer.setData("text/plain", serialized);
        e.dataTransfer.effectAllowed = "move";
        clear();
      }}
      onDragEnd={() => {
        clear();
        window.setTimeout(() => {
          suppressClick.current = false;
        }, 0);
      }}
      onDragOver={
        onMove || onUploadFiles
          ? (e) => {
              e.preventDefault();
              e.stopPropagation();
              e.dataTransfer.dropEffect =
                isInternalMoveDrag(e) || !hasExternalFiles(e) ? "move" : "copy";
              setTarget(parentDir(node.path));
            }
          : undefined
      }
      onDrop={
        onMove || onUploadFiles
          ? (e) => {
              e.preventDefault();
              e.stopPropagation();
              clear();
              acceptDrop(e, parentDir(node.path), onMove, onUploadFiles);
            }
          : undefined
      }
      onClick={() => {
        if (suppressClick.current) return;
        onOpen(node.path);
      }}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onFileContextMenu?.(node.path, e.clientX, e.clientY);
      }}
    >
      {!hidePinBadge && pinnedPaths?.has(node.path) && (
        <span className="tree-pin-icon" aria-hidden="true">
          <PinIcon />
        </span>
      )}
      <span className="tree-name">{displayName}</span>
    </div>
  );
}

function DraftFolderRow({
  depth,
  onConfirm,
  onCancel,
}: {
  depth: number;
  onConfirm: (name: string) => void;
  onCancel: () => void;
}) {
  return (
    <RenameRow
      depth={depth}
      initial="Untitled"
      isFolder
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}

function RenameRow({
  depth,
  initial,
  isFolder,
  onConfirm,
  onCancel,
}: {
  depth: number;
  initial: string;
  isFolder?: boolean;
  onConfirm: (name: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const inputRef = useRef<HTMLInputElement>(null);
  const doneRef = useRef(false);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.focus();
    el.select();
  }, []);

  const finish = (commit: boolean) => {
    if (doneRef.current) return;
    doneRef.current = true;
    const name = value.trim();
    if (commit && name) onConfirm(name);
    else onCancel();
  };

  return (
    <div
      className="tree-item tree-item-draft"
      style={{ paddingLeft: 10 + depth * 14 + (isFolder ? 0 : 14) }}
    >
      {isFolder && (
        <span className="tree-chevron">
          <ChevronIcon open={false} />
        </span>
      )}
      <input
        ref={inputRef}
        className="tree-draft-input"
        value={value}
        spellCheck={false}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            finish(true);
          } else if (e.key === "Escape") {
            e.preventDefault();
            finish(false);
          }
        }}
        onBlur={() => finish(true)}
      />
    </div>
  );
}
