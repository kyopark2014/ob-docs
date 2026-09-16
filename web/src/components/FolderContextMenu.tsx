import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

export type FolderMenuAction =
  | "new-note"
  | "new-folder"
  | "duplicate"
  | "rename"
  | "delete";

export type FileMenuAction =
  | "open-tab"
  | "duplicate"
  | "rename"
  | "delete";

export type ContextMenuState = {
  kind: "folder" | "file";
  path: string;
  x: number;
  y: number;
};

/** @deprecated use ContextMenuState */
export type FolderContextMenuState = ContextMenuState;

type Props = {
  menu: ContextMenuState;
  onFolderAction: (action: FolderMenuAction, path: string) => void;
  onFileAction: (action: FileMenuAction, path: string) => void;
  onClose: () => void;
};

const FOLDER_ITEMS: {
  action: FolderMenuAction;
  label: string;
  danger?: boolean;
  sepBefore?: boolean;
}[] = [
  { action: "new-note", label: "New note" },
  { action: "new-folder", label: "New folder" },
  { action: "duplicate", label: "Duplicate", sepBefore: true },
  { action: "rename", label: "Rename...", sepBefore: true },
  { action: "delete", label: "Delete", danger: true },
];

const FILE_ITEMS: {
  action: FileMenuAction;
  label: string;
  danger?: boolean;
  sepBefore?: boolean;
}[] = [
  { action: "open-tab", label: "Open in new tab" },
  { action: "duplicate", label: "Duplicate", sepBefore: true },
  { action: "rename", label: "Rename...", sepBefore: true },
  { action: "delete", label: "Delete", danger: true },
];

export function FolderContextMenu({
  menu,
  onFolderAction,
  onFileAction,
  onClose,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    function onPointer(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onPointer);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onPointer);
    };
  }, [onClose]);

  const pad = 8;
  const approxH = 220;
  const approxW = 200;
  const left = Math.min(menu.x, window.innerWidth - approxW - pad);
  const top = Math.min(menu.y, window.innerHeight - approxH - pad);

  const items = menu.kind === "folder" ? FOLDER_ITEMS : FILE_ITEMS;

  return createPortal(
    <div
      ref={ref}
      className="ctx-menu"
      style={{ left, top }}
      role="menu"
      onContextMenu={(e) => e.preventDefault()}
    >
      {items.map((item) => (
        <div key={item.action}>
          {item.sepBefore && <div className="ctx-sep" />}
          <button
            type="button"
            className={`ctx-item${item.danger ? " danger" : ""}`}
            role="menuitem"
            onClick={() => {
              if (menu.kind === "folder") {
                onFolderAction(item.action as FolderMenuAction, menu.path);
              } else {
                onFileAction(item.action as FileMenuAction, menu.path);
              }
              onClose();
            }}
          >
            {item.label}
          </button>
        </div>
      ))}
    </div>,
    document.body,
  );
}
