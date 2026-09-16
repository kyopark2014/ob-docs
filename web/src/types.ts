export type TreeNode = {
  name: string;
  path: string;
  type: "file" | "folder";
  ext?: string;
  children?: TreeNode[];
};

export type FilePayload = {
  path: string;
  content: string;
  word_count: number;
  char_count: number;
  backlinks: { path: string; title: string }[];
  title: string;
  tags: string[];
};

export type SearchHit = {
  path: string;
  title: string;
  tags: string[];
  score: number;
  snippet: string;
};

export type GraphPayload = {
  nodes: {
    id: string;
    label: string;
    path: string | null;
    tags: string[];
    missing?: boolean;
  }[];
  edges: { source: string; target: string }[];
};

export type OpenTab = {
  path: string;
  title: string;
  dirty?: boolean;
};

export type PanelMode = "files" | "search" | "hidden";
export type ViewMode = "preview" | "edit";
