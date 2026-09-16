import { useState } from "react";
import type { TreeNode } from "../types";
import { ChevronIcon } from "./Icons";

type Props = {
  nodes: TreeNode[];
  activePath: string | null;
  onOpen: (path: string) => void;
  depth?: number;
};

export function FileTree({ nodes, activePath, onOpen, depth = 0 }: Props) {
  return (
    <div>
      {nodes.map((node) => (
        <TreeRow
          key={node.path}
          node={node}
          activePath={activePath}
          onOpen={onOpen}
          depth={depth}
        />
      ))}
    </div>
  );
}

function TreeRow({
  node,
  activePath,
  onOpen,
  depth,
}: {
  node: TreeNode;
  activePath: string | null;
  onOpen: (path: string) => void;
  depth: number;
}) {
  const [open, setOpen] = useState(depth < 1 || node.path.startsWith("00-Inbox") || node.path === "notes");

  if (node.type === "folder") {
    return (
      <div>
        <div
          className="tree-item"
          style={{ paddingLeft: 10 + depth * 12 }}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="tree-chevron">
            <ChevronIcon open={open} />
          </span>
          <span className="tree-name">{node.name}</span>
        </div>
        {open && node.children && (
          <FileTree
            nodes={node.children}
            activePath={activePath}
            onOpen={onOpen}
            depth={depth + 1}
          />
        )}
      </div>
    );
  }

  return (
    <div
      className={`tree-item${activePath === node.path ? " active" : ""}`}
      style={{ paddingLeft: 10 + depth * 12 + 14 }}
      onClick={() => onOpen(node.path)}
    >
      <span className="tree-name">{node.name.replace(/\.md$/i, "")}</span>
    </div>
  );
}
