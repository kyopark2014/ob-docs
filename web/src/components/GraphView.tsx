import { useEffect, useRef } from "react";
import type { GraphPayload } from "../types";

type Props = {
  graph: GraphPayload | null;
  onOpen: (path: string) => void;
  onRebuild: () => void;
};

type SimNode = {
  id: string;
  label: string;
  path: string | null;
  missing?: boolean;
  x: number;
  y: number;
  vx: number;
  vy: number;
};

export function GraphView({ graph, onOpen, onRebuild }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<SimNode[]>([]);
  const edgesRef = useRef<{ source: string; target: string }[]>([]);
  const rafRef = useRef<number>(0);

  useEffect(() => {
    if (!graph) return;
    const w = canvasRef.current?.parentElement?.clientWidth || 800;
    const h = canvasRef.current?.parentElement?.clientHeight || 600;
    nodesRef.current = graph.nodes.map((n, i) => {
      const angle = (i / Math.max(graph.nodes.length, 1)) * Math.PI * 2;
      const r = 80 + (i % 5) * 28;
      return {
        id: n.id,
        label: n.label,
        path: n.path,
        missing: n.missing,
        x: w / 2 + Math.cos(angle) * r,
        y: h / 2 + Math.sin(angle) * r,
        vx: 0,
        vy: 0,
      };
    });
    edgesRef.current = graph.edges;
  }, [graph]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resize = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = parent.clientWidth * dpr;
      canvas.height = parent.clientHeight * dpr;
      canvas.style.width = `${parent.clientWidth}px`;
      canvas.style.height = `${parent.clientHeight}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener("resize", resize);

    const tick = () => {
      const parent = canvas.parentElement;
      if (!parent) return;
      const w = parent.clientWidth;
      const h = parent.clientHeight;
      const nodes = nodesRef.current;
      const byId = new Map(nodes.map((n) => [n.id, n]));

      // forces
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let dist = Math.hypot(dx, dy) || 1;
          const force = 1200 / (dist * dist);
          dx = (dx / dist) * force;
          dy = (dy / dist) * force;
          a.vx += dx;
          a.vy += dy;
          b.vx -= dx;
          b.vy -= dy;
        }
      }
      for (const e of edgesRef.current) {
        const a = byId.get(e.source);
        const b = byId.get(e.target);
        if (!a || !b) continue;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.hypot(dx, dy) || 1;
        const force = (dist - 140) * 0.01;
        a.vx += (dx / dist) * force;
        a.vy += (dy / dist) * force;
        b.vx -= (dx / dist) * force;
        b.vy -= (dy / dist) * force;
      }
      for (const n of nodes) {
        n.vx += (w / 2 - n.x) * 0.002;
        n.vy += (h / 2 - n.y) * 0.002;
        n.vx *= 0.85;
        n.vy *= 0.85;
        n.x += n.vx;
        n.y += n.vy;
        n.x = Math.max(24, Math.min(w - 24, n.x));
        n.y = Math.max(24, Math.min(h - 24, n.y));
      }

      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = "rgba(16,163,127,0.35)";
      ctx.lineWidth = 1;
      for (const e of edgesRef.current) {
        const a = byId.get(e.source);
        const b = byId.get(e.target);
        if (!a || !b) continue;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      for (const n of nodes) {
        ctx.beginPath();
        ctx.fillStyle = n.missing ? "#e25555" : "#10a37f";
        ctx.arc(n.x, n.y, n.missing ? 5 : 7, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = "#dadada";
        ctx.font = "12px IBM Plex Sans, sans-serif";
        ctx.fillText(n.label, n.x + 10, n.y + 4);
      }

      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    const onClick = (ev: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const y = ev.clientY - rect.top;
      for (const n of nodesRef.current) {
        if (Math.hypot(n.x - x, n.y - y) < 12 && n.path) {
          onOpen(n.path);
          break;
        }
      }
    };
    canvas.addEventListener("click", onClick);

    return () => {
      cancelAnimationFrame(rafRef.current);
      window.removeEventListener("resize", resize);
      canvas.removeEventListener("click", onClick);
    };
  }, [graph, onOpen]);

  return (
    <div className="graph-wrap">
      <div className="graph-toolbar">
        <button type="button" onClick={onRebuild}>
          Rebuild
        </button>
      </div>
      <canvas ref={canvasRef} />
    </div>
  );
}
