/** Obsidian-like wiki link resolution against the in-memory file tree. */

export type WikiFile = { path: string; name: string };

export function normWikiKey(name: string): string {
  let s = name.normalize("NFC").trim().replace(/\\/g, "/");
  if (s.toLowerCase().endsWith(".md")) s = s.slice(0, -3);
  return s.toLowerCase();
}

export function noteParentDir(path: string): string {
  const i = path.replace(/\\/g, "/").lastIndexOf("/");
  return i >= 0 ? path.slice(0, i) : "";
}

function joinVaultPath(parent: string, rel: string): string {
  const parts = [
    ...(parent ? parent.replace(/\\/g, "/").split("/") : []),
    ...rel.normalize("NFC").replace(/\\/g, "/").split("/"),
  ];
  const out: string[] = [];
  for (const part of parts) {
    if (!part || part === ".") continue;
    if (part === "..") {
      out.pop();
      continue;
    }
    out.push(part);
  }
  return out.join("/");
}

function pathKey(path: string): string {
  return normWikiKey(path.replace(/\\/g, "/"));
}

function preferSameFolder(hits: WikiFile[], fromPath?: string | null): WikiFile | null {
  if (!hits.length) return null;
  if (hits.length === 1 || !fromPath) return hits[0];
  const parent = noteParentDir(fromPath);
  const same = hits.filter((f) => noteParentDir(f.path) === parent);
  return same[0] || hits[0];
}

/**
 * Resolve [[target]] to a vault-relative markdown path.
 * Mirrors application.vault_index.resolve_link for the client tree.
 */
export function resolveWikiTarget(
  target: string,
  files: WikiFile[],
  fromPath?: string | null,
): string | null {
  const raw = target.normalize("NFC").trim().replace(/\\/g, "/");
  if (!raw) return null;
  const needle = normWikiKey(raw);
  const basename = normWikiKey(raw.split("/").pop() || raw);
  const mdFiles = files.filter(
    (f) => f.path.toLowerCase().endsWith(".md") || f.name.toLowerCase().endsWith(".md"),
  );

  const exactPath = (want: string): WikiFile | undefined => {
    const key = normWikiKey(want);
    return mdFiles.find((f) => pathKey(f.path) === key);
  };

  // 1. Vault-absolute path
  let hit = exactPath(raw);
  if (hit) return hit.path;

  // 2. Relative to current note folder
  if (fromPath) {
    const joined = joinVaultPath(noteParentDir(fromPath), raw);
    hit = exactPath(joined);
    if (hit) return hit.path;
  }

  // 3. Path suffix match
  if (needle.includes("/")) {
    const suffixHits = mdFiles.filter((f) => {
      const pk = pathKey(f.path);
      return pk === needle || pk.endsWith("/" + needle);
    });
    const preferred = preferSameFolder(suffixHits, fromPath);
    if (preferred) return preferred.path;
  }

  // 4. Basename (same folder first), including last segment of path-style links
  const stemHits = mdFiles.filter((f) => normWikiKey(f.name) === basename);
  if (stemHits.length) {
    if (fromPath) {
      const parent = noteParentDir(fromPath);
      const sameFolder = stemHits.filter((f) => noteParentDir(f.path) === parent);
      if (sameFolder.length) return sameFolder[0].path;
    }
    if (stemHits.length === 1 || !needle.includes("/")) {
      return preferSameFolder(stemHits, fromPath)!.path;
    }
  }

  // 5. Full-needle stem match (no path) already covered; try title-like path includes last
  if (!needle.includes("/")) {
    const includes = mdFiles.filter((f) => pathKey(f.path).includes(needle));
    if (includes.length === 1) return includes[0].path;
    const preferred = preferSameFolder(includes, fromPath);
    if (preferred && normWikiKey(preferred.name) === needle) return preferred.path;
  }

  return null;
}
