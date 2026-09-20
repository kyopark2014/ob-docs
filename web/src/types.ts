export type TreeNode = {
  name: string;
  path: string;
  type: "file" | "folder";
  ext?: string;
  children?: TreeNode[];
  note_id?: string;
  title?: string;
  size_bytes?: number;
  created_at?: string;
  updated_at?: string;
};

export type FilePayload = {
  path: string;
  content: string;
  word_count: number;
  char_count: number;
  backlinks: { path: string; title: string }[];
  title: string;
  tags: string[];
  note_id?: string | null;
  size_bytes?: number;
  created_at?: string | null;
  updated_at?: string | null;
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

export type PanelMode = "files" | "search" | "meeting" | "hidden";
export type ViewMode = "preview" | "edit";
