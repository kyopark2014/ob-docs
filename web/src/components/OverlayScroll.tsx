import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent,
  type MouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type UIEvent,
} from "react";

type Props = {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  /** Cap thumb to this fraction of the track (agentic-work short-pill look). */
  maxThumbRatio?: number;
  /** Scale natural thumb height (user asked ~2/3 of the long native bar). */
  thumbScale?: number;
  onContextMenu?: (e: MouseEvent<HTMLDivElement>) => void;
  onDragOver?: (e: DragEvent<HTMLDivElement>) => void;
  onDrop?: (e: DragEvent<HTMLDivElement>) => void;
};

/**
 * Overlay scrollbar matching agentic-work: rounded pill, transparent track.
 * Thumb height is shortened vs native so it stays closer to the short pill in
 * agentic-work even when the panel content only barely overflows.
 */
export function OverlayScroll({
  children,
  className = "",
  style,
  maxThumbRatio = 0.28,
  thumbScale = 2 / 3,
  onContextMenu,
  onDragOver,
  onDrop,
}: Props) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState({ top: 0, height: 0, visible: false });
  const dragging = useRef<{ startY: number; startTop: number } | null>(null);

  const sync = useCallback(() => {
    const el = viewportRef.current;
    if (!el) return;
    const { clientHeight, scrollHeight, scrollTop } = el;
    const overflow = scrollHeight - clientHeight;
    if (overflow <= 1) {
      setThumb({ top: 0, height: 0, visible: false });
      return;
    }
    const natural = (clientHeight / scrollHeight) * clientHeight;
    const height = Math.max(
      24,
      Math.min(natural * thumbScale, clientHeight * maxThumbRatio),
    );
    const maxTop = clientHeight - height;
    const top = maxTop <= 0 ? 0 : (scrollTop / overflow) * maxTop;
    setThumb({ top, height, visible: true });
  }, [maxThumbRatio, thumbScale]);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    sync();
    const ro = new ResizeObserver(() => sync());
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, [sync, children]);

  const onScroll = useCallback(
    (_e: UIEvent<HTMLDivElement>) => {
      sync();
    },
    [sync],
  );

  const onThumbPointerDown = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      const el = viewportRef.current;
      if (!el) return;
      dragging.current = { startY: e.clientY, startTop: thumb.top };
      e.currentTarget.setPointerCapture(e.pointerId);

      const onMove = (ev: PointerEvent) => {
        const drag = dragging.current;
        const view = viewportRef.current;
        if (!drag || !view) return;
        const { clientHeight, scrollHeight } = view;
        const overflow = scrollHeight - clientHeight;
        if (overflow <= 0) return;
        const height = Math.max(
          24,
          Math.min(
            (clientHeight / scrollHeight) * clientHeight * thumbScale,
            clientHeight * maxThumbRatio,
          ),
        );
        const maxTop = clientHeight - height;
        const nextTop = Math.min(
          maxTop,
          Math.max(0, drag.startTop + (ev.clientY - drag.startY)),
        );
        view.scrollTop = maxTop <= 0 ? 0 : (nextTop / maxTop) * overflow;
      };
      const onUp = (ev: PointerEvent) => {
        dragging.current = null;
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        try {
          (e.target as HTMLElement).releasePointerCapture?.(ev.pointerId);
        } catch {
          /* already released */
        }
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [maxThumbRatio, thumb.top, thumbScale],
  );

  return (
    <div
      className={`overlay-scroll${className ? ` ${className}` : ""}`}
      style={style}
      onContextMenu={onContextMenu}
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <div
        ref={viewportRef}
        className="overlay-scroll-viewport"
        onScroll={onScroll}
      >
        {children}
      </div>
      {thumb.visible && (
        <div className="overlay-scroll-rail" aria-hidden="true">
          <div
            className="overlay-scroll-thumb"
            style={{ top: thumb.top, height: thumb.height }}
            onPointerDown={onThumbPointerDown}
          />
        </div>
      )}
    </div>
  );
}
