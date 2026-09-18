import { useEffect, useRef, useState } from "react";
import type { MeetingLogController } from "../meetingLog/useMeetingLog";
import { DEFAULT_MEETING_TITLE, SPEAKERS } from "../meetingLog/config";

type Props = {
  meeting: MeetingLogController;
  onClose: () => void;
  onSaveVault: () => void;
  onClearConfirm: () => Promise<boolean>;
};

export function MeetingLogSidebar({
  meeting,
  onClose,
  onSaveVault,
  onClearConfirm,
}: Props) {
  const {
    status,
    listening,
    batchBusy,
    autoSpeaker,
    pinnedSpeaker,
    view,
    setView,
    title,
    setTitle,
    canSaveVault,
    savingVault,
    toggleListening,
    selectSpeakerMode,
    copyLog,
  } = meeting;

  const [editingTitle, setEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const titleInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!editingTitle) setDraftTitle(title);
  }, [title, editingTitle]);

  useEffect(() => {
    if (!editingTitle) return;
    const el = titleInputRef.current;
    if (!el) return;
    el.focus();
    el.select();
  }, [editingTitle]);

  const commitTitle = () => {
    const next = draftTitle.trim() || DEFAULT_MEETING_TITLE;
    setTitle(next);
    setDraftTitle(next);
    setEditingTitle(false);
  };

  const cancelTitleEdit = () => {
    setDraftTitle(title);
    setEditingTitle(false);
  };

  return (
    <div className="meeting-sidebar">
      <div className="sidebar-header meeting-sidebar-header">
        <span>회의 로그</span>
        <div className="meeting-head-actions">
          <button type="button" className="link-btn" onClick={() => void copyLog()}>
            복사
          </button>
          <button
            type="button"
            className="link-btn"
            onClick={() => {
              void onClearConfirm().then((ok) => {
                if (ok) meeting.clearLog();
              });
            }}
          >
            지우기
          </button>
          <button
            type="button"
            className="link-btn"
            onClick={() => {
              meeting.stopListening({ autoBatch: false });
              onClose();
            }}
          >
            닫기
          </button>
        </div>
      </div>

      <div className="meeting-sidebar-body">
        <div className="meeting-title-field">
          {editingTitle ? (
            <input
              ref={titleInputRef}
              type="text"
              className="meeting-title-input"
              value={draftTitle}
              placeholder={DEFAULT_MEETING_TITLE}
              maxLength={60}
              aria-label="회의 제목 수정"
              onChange={(e) => setDraftTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  commitTitle();
                } else if (e.key === "Escape") {
                  e.preventDefault();
                  cancelTitleEdit();
                }
              }}
              onBlur={cancelTitleEdit}
            />
          ) : (
            <button
              type="button"
              className="meeting-title-display"
              title="클릭하여 제목 수정"
              aria-label="회의 제목 수정"
              onClick={() => {
                setDraftTitle(title);
                setEditingTitle(true);
              }}
            >
              {title.trim() || DEFAULT_MEETING_TITLE}
            </button>
          )}
        </div>

        <p className="meeting-hint">
          마이크를 누르면 실시간으로 기록합니다. 녹음이 끝나면 전체 음성을 AWS
          Transcribe로 다시 보내 회의 내용을 정리합니다.
        </p>

        <div className="meeting-mic-wrap">
          <button
            type="button"
            className={`meeting-mic-btn${listening ? " recording" : ""}`}
            aria-label={listening ? "회의 기록 중지" : "회의 기록 시작"}
            title={listening ? "회의 기록 중지" : "회의 기록 시작"}
            onClick={toggleListening}
            disabled={batchBusy}
          >
            <svg className="meeting-mic-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path
                fill="currentColor"
                d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.9V21h2v-3.1A7 7 0 0 0 19 11h-2z"
              />
            </svg>
          </button>
        </div>

        <p className="meeting-status">{status}</p>

        <div className="meeting-speaker-row" role="group" aria-label="화자 선택">
          <span className="meeting-speaker-label">화자</span>
          {SPEAKERS.map((s) => (
            <button
              key={s}
              type="button"
              className={`meeting-speaker-chip${
                !autoSpeaker && pinnedSpeaker === s ? " active" : ""
              }`}
              data-speaker={s}
              aria-pressed={!autoSpeaker && pinnedSpeaker === s}
              onClick={() => selectSpeakerMode(s)}
            >
              {s}
            </button>
          ))}
          <button
            type="button"
            className={`meeting-speaker-chip auto${autoSpeaker ? " active" : ""}`}
            aria-pressed={autoSpeaker}
            onClick={() => selectSpeakerMode("auto")}
          >
            자동
          </button>
        </div>

        <div className="meeting-controls">
          <div className="meeting-view-seg" role="group" aria-label="기록 보기">
            {(
              [
                ["live", "실시간"],
                ["batch", "배치"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={`meeting-view-btn${view === id ? " active" : ""}`}
                aria-pressed={view === id}
                onClick={() => setView(id)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {canSaveVault && (
          <button
            type="button"
            className="meeting-vault-btn"
            disabled={savingVault || listening || batchBusy}
            onClick={onSaveVault}
          >
            {savingVault ? "Vault 저장 중…" : "Vault 저장"}
          </button>
        )}
      </div>
    </div>
  );
}
