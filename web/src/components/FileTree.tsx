import { useEffect, useRef, useState } from "react";
import type { TreeNode } from "../types";
import { ChevronIcon } from "./Icons";

export type DraftFolder = {
  parentPath: string; // "" = vault root
};

type Props = {
  nodes: TreeNode[];
  activePath: string | null;
  selectedFolder?: string | null;
  onOpen: (path: string) => void;
  onSelectFolder?: (path: string) => void;
  onFolderContextMenu?: (path: string, x: number, y: number) => void;
  onFileContextMenu?: (path: string, x: number, y: number) => void;
  draftFolder?: DraftFolder | null;
  onDraftConfirm?: (name: string) => void;
  onDraftCancel?: () => void;
  renamingPath?: string | null;
  onRenameConfirm?: (path: string, name: string) => void;
  onRenameCancel?: () => void;
  depth?: number;
  forceOpenPaths?: Set<string>;
};

export function FileTree(props: Props) {
  const {
    nodes,
    draftFolder = null,
    onDraftConfirm,
    onDraftCancel,
    depth = 0,
  } = props;

  const showDraftHere =
    !!draftFolder && depth === 0 && draftFolder.parentPath === "";

  return (
    <div className={depth === 0 ? "file-tree" : undefined}>
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
  draftFolder = null,
  onDraftConfirm,
  onDraftCancel,
  renamingPath = null,
  onRenameConfirm,
  onRenameCancel,
  depth = 0,
  forceOpenPaths,
}: Props & { node: TreeNode }) {
  const shouldForce =
    forceOpenPaths?.has(node.path) ||
    (draftFolder?.parentPath === node.path) ||
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
            className={`tree-item${isSelected ? " selected" : ""}`}
            style={{ paddingLeft: 10 + depth * 14 }}
            onClick={() => {
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
            <span className="tree-name">{node.name}</span>
          </div>
        )}
        {open && (
          <div className="tree-children">
            {node.children && (
              <FileTree
                nodes={node.children}
                activePath={activePath}
                selectedFolder={selectedFolder}
                onOpen={onOpen}
                onSelectFolder={onSelectFolder}
                onFolderContextMenu={onFolderContextMenu}
                onFileContextMenu={onFileContextMenu}
                draftFolder={draftFolder}
                onDraftConfirm={onDraftConfirm}
                onDraftCancel={onDraftCancel}
                renamingPath={renamingPath}
                onRenameConfirm={onRenameConfirm}
                onRenameCancel={onRenameCancel}
                depth={depth + 1}
                forceOpenPaths={forceOpenPaths}
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
      onClick={() => onOpen(node.path)}
      onContextMenu={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onFileContextMenu?.(node.path, e.clientX, e.clientY);
      }}
    >
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
