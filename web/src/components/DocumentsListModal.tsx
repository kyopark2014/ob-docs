import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { api, type DocumentsDocument } from "../api";

interface Props {
  onClose: () => void;
  /** ``project`` → OCR/Projects, ``drawing`` → OCR/Drawings */
  kind?: "project" | "drawing";
  /** Called after markdown is saved into the vault (e.g. refreshTree + openFile). */
  onCopied?: (vaultPath: string) => void | Promise<void>;
}

function formatBytes(bytes: number | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatCreatedAt(value: string | undefined): string | null {
  const raw = (value || "").trim();
  if (!raw) return null;
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function documentSublineParts(
  doc: DocumentsDocument,
  extra: Array<string | null | undefined> = [],
): string {
  const timestamp =
    doc.status === "extracted" || doc.status === "saved"
      ? formatCreatedAt(doc.extracted_at) || formatCreatedAt(doc.created_at)
      : formatCreatedAt(doc.created_at);
  const parts = [timestamp, ...extra, formatBytes(doc.bytes)].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : "—";
}

function openInNewTab(url: string) {
  window.open(url, "_blank", "noopener,noreferrer");
}

function documentKey(doc: DocumentsDocument): string {
  return doc.filename || doc.md_file || doc.display_name || "doc";
}

export function DocumentsListModal({
  onClose,
  kind = "project",
  onCopied,
}: Props) {
  const [documents, setDocuments] = useState<DocumentsDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [copyingKey, setCopyingKey] = useState<string | null>(null);
  const title = kind === "drawing" ? "Drawings" : "Projects";
  const vaultFolder = kind === "drawing" ? "OCR/Drawings" : "OCR/Projects";
  const emptyHint =
    kind === "drawing"
      ? "등록된 Drawing 문서가 없습니다. Configure에서 Drawing 문서를 추가한 뒤 Sync 하세요."
      : "등록된 Project 문서가 없습니다. Configure에서 Project 문서를 추가한 뒤 Sync 하세요.";

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data =
          kind === "drawing"
            ? await api.getDocumentsDrawingList(true)
            : await api.getDocumentsProjectList(true);
        if (cancelled) return;
        setDocuments(data.documents ?? []);
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
  }, [kind]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  function openMarkdown(doc: DocumentsDocument) {
    const url = doc.md_viewer_url;
    if (!url) return;
    openInNewTab(url);
  }

  function openPdf(doc: DocumentsDocument) {
    const url = doc.pdf_api_url || doc.pdf_url;
    if (!url) return;
    openInNewTab(url);
  }

  async function copyMarkdownToVault(doc: DocumentsDocument) {
    const filename = (doc.filename || "").trim();
    if (!filename) {
      setError("복사할 파일명이 없습니다.");
      return;
    }
    if (!doc.md_available) {
      setError("추출된 Markdown이 없습니다. Documents Sync를 먼저 실행하세요.");
      return;
    }
    const key = documentKey(doc);
    setCopyingKey(key);
    setError(null);
    try {
      const result = await api.copyDocumentsToVault(filename, kind);
      await onCopied?.(result.path);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setCopyingKey(null);
    }
  }

  async function deleteDocument(doc: DocumentsDocument) {
    const filename = (doc.filename || "").trim();
    if (!filename) {
      setError("삭제할 파일명이 없습니다.");
      return;
    }
    const label =
      doc.display_name ||
      doc.title ||
      doc.original_filename ||
      filename;
    const kindLabel = kind === "drawing" ? "Drawing" : "Project";
    const confirmed = window.confirm(
      `"${label}" ${kindLabel} 문서와 관련 파일(원본·JSON·Markdown)을 삭제할까요?\n이 작업은 되돌릴 수 없습니다.`,
    );
    if (!confirmed) return;

    const key = documentKey(doc);
    setDeletingKey(key);
    setError(null);
    try {
      await api.deleteDocumentsDocument(filename, kind);
      setDocuments((prev) =>
        prev.filter((d) => (d.filename || "").trim() !== filename),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeletingKey(null);
    }
  }

  return createPortal(
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="documents-doc-list-title"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-card documents-doc-list-modal">
        <div className="modal-header">
          <h2 id="documents-doc-list-title" className="modal-title">
            {title}
          </h2>
          <button
            type="button"
            className="modal-close"
            aria-label="닫기"
            onClick={onClose}
          >
            ×
          </button>
        </div>
        <div className="modal-body">
          {loading ? (
            <p className="documents-configure-muted">문서 목록을 불러오는 중…</p>
          ) : documents.length === 0 ? (
            error ? (
              <p
                className="modal-detail"
                role="alert"
                style={{ color: "var(--danger, #e25555)" }}
              >
                {error}
              </p>
            ) : (
              <p className="documents-configure-docs-empty">{emptyHint}</p>
            )
          ) : (
            <>
              {error ? (
                <p
                  className="modal-detail"
                  role="alert"
                  style={{ color: "var(--danger, #e25555)" }}
                >
                  {error}
                </p>
              ) : null}
              <ul className="documents-doc-list">
                {documents.map((doc) => {
                  const key = documentKey(doc);
                  const itemTitle =
                    doc.display_name ||
                    doc.title ||
                    doc.original_filename ||
                    doc.filename ||
                    doc.md_file ||
                    "document";
                  const canDelete = Boolean((doc.filename || "").trim());
                  const isDeleting = deletingKey === key;
                  const isCopying = copyingKey === key;
                  const canMd = Boolean(doc.md_available && doc.md_viewer_url);
                  const canPdf = Boolean(
                    doc.pdf_available && (doc.pdf_api_url || doc.pdf_url),
                  );
                  const canCopy = Boolean(doc.md_available);
                  return (
                    <li key={key} className="documents-doc-list-item">
                      <div className="documents-doc-list-meta">
                        <span
                          className="documents-doc-list-name"
                          title={itemTitle}
                        >
                          {itemTitle}
                        </span>
                        <span className="documents-doc-list-sub">
                          {documentSublineParts(doc, [doc.status || "—"])}
                        </span>
                      </div>
                      <div className="documents-doc-list-actions">
                        <button
                          type="button"
                          className="documents-doc-list-btn"
                          disabled={!canMd || isDeleting || isCopying}
                          title={
                            canMd
                              ? "Markdown viewer (새 탭)"
                              : "Markdown 없음 (Sync 필요)"
                          }
                          onClick={() => openMarkdown(doc)}
                        >
                          Markdown
                        </button>
                        <button
                          type="button"
                          className="documents-doc-list-btn"
                          disabled={!canPdf || isDeleting || isCopying}
                          title={
                            canPdf
                              ? "PDF (새 탭)"
                              : "PDF 파일을 찾을 수 없습니다"
                          }
                          onClick={() => openPdf(doc)}
                        >
                          PDF
                        </button>
                        <button
                          type="button"
                          className="documents-doc-list-btn documents-doc-list-btn-success"
                          disabled={!canCopy || isDeleting || isCopying}
                          title={
                            canCopy
                              ? `vault ${vaultFolder}에 Markdown 저장`
                              : "Markdown 없음 (Sync 필요)"
                          }
                          onClick={() => void copyMarkdownToVault(doc)}
                        >
                          {isCopying ? "저장 중…" : "복사"}
                        </button>
                        <button
                          type="button"
                          className="documents-doc-list-btn documents-doc-list-btn-danger"
                          disabled={!canDelete || isDeleting || isCopying}
                          title={
                            canDelete
                              ? "원본·JSON·Markdown 및 목록에서 삭제"
                              : "삭제할 파일명이 없습니다"
                          }
                          onClick={() => void deleteDocument(doc)}
                        >
                          {isDeleting ? "삭제 중…" : "삭제"}
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
        <div className="modal-footer">
          <button
            type="button"
            className="modal-btn modal-btn-cancel"
            onClick={onClose}
          >
            닫기
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
