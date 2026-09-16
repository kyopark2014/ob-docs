const PINNED_KEY = "ob-docs:pinned-paths";

export function getPinnedPaths(): string[] {
  try {
    const raw = localStorage.getItem(PINNED_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((p): p is string => typeof p === "string" && p.length > 0);
  } catch {
    return [];
  }
}

export function setPinnedPaths(paths: string[]): void {
  try {
    localStorage.setItem(PINNED_KEY, JSON.stringify(paths));
  } catch {
    /* ignore */
  }
}

export function togglePinnedPath(path: string, current: string[]): string[] {
  if (current.includes(path)) {
    return current.filter((p) => p !== path);
  }
  return [...current, path];
}

export function rewritePinnedPaths(paths: string[], from: string, to: string): string[] {
  return paths.map((p) => {
    if (p === from) return to;
    if (p.startsWith(from + "/")) return to + p.slice(from.length);
    return p;
  });
}

export function removePinnedPaths(paths: string[], deleted: string): string[] {
  return paths.filter((p) => p !== deleted && !p.startsWith(deleted + "/"));
}
