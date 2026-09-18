import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

export type TabMenuAction = "close" | "close-others" | "close-after" | "close-all";

export type TabContextMenuState = {
  path: string;
  x: number;
  y: number;
};

type Props = {
  menu: TabContextMenuState;
  /** Disable items that would no-op (e.g. only one tab). */
  disableOthers?: boolean;
  disableAfter?: boolean;
  onAction: (action: TabMenuAction, path: string) => void;
  onClose: () => void;
};

const ITEMS: { action: TabMenuAction; label: string; sepBefore?: boolean }[] = [
  { action: "close", label: "Close" },
  { action: "close-others", label: "Close others", sepBefore: true },
  { action: "close-after", label: "Close tabs after" },
  { action: "close-all", label: "Close all" },
];

export function TabContextMenu({
  menu,
  disableOthers = false,
  disableAfter = false,
  onAction,
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
  const approxH = 160;
  const approxW = 200;
  const left = Math.min(menu.x, window.innerWidth - approxW - pad);
  const top = Math.min(menu.y, window.innerHeight - approxH - pad);

  return createPortal(
    <div
      ref={ref}
      className="ctx-menu"
      style={{ left, top }}
      role="menu"
      onContextMenu={(e) => e.preventDefault()}
    >
      {ITEMS.map((item) => {
        const disabled =
          (item.action === "close-others" && disableOthers) ||
          (item.action === "close-after" && disableAfter);
        return (
          <div key={item.action}>
            {item.sepBefore && <div className="ctx-sep" />}
            <button
              type="button"
              className="ctx-item"
              role="menuitem"
              disabled={disabled}
              onClick={() => {
                if (disabled) return;
                onAction(item.action, menu.path);
                onClose();
              }}
            >
              {item.label}
            </button>
          </div>
        );
      })}
    </div>,
    document.body,
  );
}
