import { useEffect, useRef } from "react";
import { formatTime, speakerClass } from "../meetingLog/format";
import type { MeetingLogController } from "../meetingLog/useMeetingLog";

const EMPTY_HINT =
  "회의 내용이 여기에 쌓입니다. 스크롤로 이전 내용을 볼 수 있습니다.";

type Props = {
  meeting: MeetingLogController;
};

export function MeetingLogView({ meeting }: Props) {
  const { entries, batchEntries, view, interim, cycleSpeaker, recordedAt } = meeting;
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    const pin = () => {
      el.scrollTop = el.scrollHeight;
    };
    pin();
    requestAnimationFrame(() => requestAnimationFrame(pin));
  }, [entries, batchEntries, interim, view]);

  return (
    <div className="meeting-main">
      <div className="meeting-main-toolbar">
        <div className="breadcrumb">{meeting.title.trim() || "회의 로그"}</div>
      </div>
      <div className="meeting-feed">
        <div ref={logRef} className="meeting-log" aria-live="polite">
          {view === "batch" ? (
            batchEntries.length === 0 ? (
              <p className="meeting-empty">
                아직 배치 변환 결과가 없습니다. 녹음을 멈추면 자동으로 정리됩니다.
              </p>
            ) : (
              batchEntries.map((entry, index) => (
                <LogCard
                  key={`batch-${index}`}
                  speaker={entry.speaker}
                  text={entry.text}
                  time={
                    entry.start != null
                      ? formatTime(
                          recordedAt.getTime() + Math.max(0, entry.start) * 1000,
                        )
                      : undefined
                  }
                />
              ))
            )
          ) : entries.length === 0 && !interim ? (
            <p className="meeting-empty">{EMPTY_HINT}</p>
          ) : (
            <>
              {entries.map((entry) => (
                <LogCard
                  key={entry.id}
                  speaker={entry.speaker}
                  text={entry.text}
                  time={formatTime(entry.at)}
                  onCycleSpeaker={() => cycleSpeaker(entry.id)}
                />
              ))}
              {interim && (
                <LogCard
                  speaker={interim.speaker}
                  text={interim.text}
                  time="인식 중"
                  interim
                />
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function LogCard({
  speaker,
  text,
  time,
  interim,
  onCycleSpeaker,
}: {
  speaker: string;
  text: string;
  time?: string;
  interim?: boolean;
  onCycleSpeaker?: () => void;
}) {
  return (
    <article className={`meeting-log-item${interim ? " interim" : ""}`}>
      <button
        type="button"
        className={`meeting-speaker-badge ${speakerClass(speaker)}`}
        title={onCycleSpeaker ? "화자 바꾸기" : undefined}
        disabled={!onCycleSpeaker}
        onClick={onCycleSpeaker}
      >
        {speaker}
      </button>
      <div>
        <p className="meeting-log-text">{text}</p>
      </div>
      {time != null && <time className="meeting-log-time">{time}</time>}
    </article>
  );
}
