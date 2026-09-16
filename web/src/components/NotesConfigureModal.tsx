import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, type NotesFolderOption } from "../api";

interface Props {
  onClose: () => void;
}

export function NotesConfigureModal({ onClose }: Props) {
  const [folders, setFolders] = useState<string[]>([]);
  const [available, setAvailable] = useState<NotesFolderOption[]>([]);
  const [includeMissing, setIncludeMissing] = useState(true);
  const [notesDir, setNotesDir] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await api.getNotesGraphSources();
        if (cancelled) return;
        setNotesDir(data.notes_dir || "");
        setFolders([...(data.folders || [])]);
        setAvailable(data.available_folders || []);
        setIncludeMissing(data.include_missing !== false);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose]);

  function toggleFolder(path: string) {
    setFolders((prev) =>
      prev.includes(path) ? prev.filter((p) => p !== path) : [...prev, path],
    );
    setSuccess(null);
  }

  async function handleSave() {
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const saved = await api.putNotesGraphSources({
        folders,
        include_missing: includeMissing,
      });
      setFolders([...(saved.folders || [])]);
      setAvailable(saved.available_folders || []);
      setIncludeMissing(saved.include_missing !== false);
      const parts: string[] = [];
      if (saved.folders.length > 0) {
        parts.push(`폴더 ${saved.folders.length}개 포함`);
      } else {
        parts.push("전체 vault 포함");
      }
      parts.push(
        includeMissing ? "미해결 링크 표시" : "미해결 링크 숨김",
      );
      setSuccess(parts.join(". ") + ". Sync를 실행하면 그래프에 반영됩니다.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return createPortal(
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="notes-configure-title"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div className="modal-card notes-configure-modal">
        <div className="modal-header">
          <h2 id="notes-configure-title" className="modal-title">
            Notes Configure
          </h2>
          <button
            type="button"
            className="modal-close"
            aria-label="닫기"
            disabled={busy}
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <div className="modal-body">
          {loading ? (
            <p className="notes-configure-muted">불러오는 중…</p>
          ) : (
            <>
              <p className="notes-configure-muted">
                vault 경로: {notesDir || "(unknown)"}
              </p>
              <label className="notes-configure-toggle">
                <span>미해결 위키링크(missing) 노드 포함</span>
                <input
                  type="checkbox"
                  checked={includeMissing}
                  disabled={busy}
                  onChange={(e) => {
                    setIncludeMissing(e.target.checked);
                    setSuccess(null);
                  }}
                />
              </label>
              <div className="notes-configure-section-label">
                포함할 폴더 (비우면 전체)
              </div>
              {available.length === 0 ? (
                <p className="notes-configure-muted">
                  최상위 폴더가 없습니다. vault 루트 전체가 대상입니다.
                </p>
              ) : (
                <ul className="notes-configure-folder-list">
                  {available.map((folder) => {
                    const checked = folders.includes(folder.path);
                    return (
                      <li key={folder.path}>
                        <label className="notes-configure-folder-item">
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={busy}
                            onChange={() => toggleFolder(folder.path)}
                          />
                          <span>{folder.name}/</span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}
          {error ? (
            <p className="modal-detail" role="alert" style={{ color: "var(--danger, #e25555)" }}>
              {error}
            </p>
          ) : null}
          {success ? <p className="notes-configure-success">{success}</p> : null}
        </div>
        <div className="modal-footer">
          <button
            type="button"
            className="modal-btn modal-btn-cancel"
            disabled={busy}
            onClick={onClose}
          >
            닫기
          </button>
          <button
            type="button"
            className="modal-btn modal-btn-confirm"
            disabled={busy || loading}
            onClick={() => void handleSave()}
          >
            {busy ? "저장 중…" : "저장"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
