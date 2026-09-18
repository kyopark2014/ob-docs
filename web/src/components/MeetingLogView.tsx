import { useEffect, useRef } from "react";
import { formatOffset, formatTime, speakerClass } from "../meetingLog/format";
import type { MeetingLogController } from "../meetingLog/useMeetingLog";

const EMPTY_HINT =
  "회의 내용이 여기에 쌓입니다. 스크롤로 이전 내용을 볼 수 있습니다.";

type Props = {
  meeting: MeetingLogController;
};

export function MeetingLogView({ meeting }: Props) {
  const { entries, batchEntries, view, interim, cycleSpeaker } = meeting;
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
        <div className="breadcrumb">회의 로그</div>
      </div>
      <div className="meeting-feed">
        {view === "compare" && (
          <div className="meeting-compare-legend">
            왼쪽이 실시간, 오른쪽이 전체 변환입니다. 화자가 다르면 행이 강조됩니다.
          </div>
        )}
        <div ref={logRef} className="meeting-log" aria-live="polite">
          {view === "compare" ? (
            <CompareRows live={entries} batch={batchEntries} />
          ) : view === "batch" ? (
            batchEntries.length === 0 ? (
              <p className="meeting-empty">
                아직 전체 변환 결과가 없습니다. 녹음을 멈춘 뒤 전체 변환을 기다리거나
                다시 실행하세요.
              </p>
            ) : (
              batchEntries.map((entry, index) => (
                <LogCard
                  key={`batch-${index}`}
                  speaker={entry.speaker}
                  text={entry.text}
                  time={
                    entry.start != null ? formatOffset(entry.start) : undefined
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
                  time={
                    entry.start != null
                      ? formatOffset(entry.start)
                      : formatTime(entry.at)
                  }
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

function CompareRows({
  live,
  batch,
}: {
  live: MeetingLogController["entries"];
  batch: MeetingLogController["batchEntries"];
}) {
  if (!live.length && !batch.length) {
    return (
      <p className="meeting-empty">
        비교할 기록이 없습니다. 실시간 녹음 후 전체 변환을 실행하세요.
      </p>
    );
  }
  const n = Math.max(live.length, batch.length);
  return (
    <>
      {Array.from({ length: n }, (_, i) => {
        const left = live[i];
        const right = batch[i];
        const mismatch = Boolean(left && right && left.speaker !== right.speaker);
        return (
          <article
            key={`cmp-${i}`}
            className={`meeting-compare-row${mismatch ? " mismatch" : ""}`}
          >
            <div className="meeting-compare-col">
              <p className="meeting-compare-label">실시간 {left ? i + 1 : ""}</p>
              {left ? (
                <>
                  <span
                    className={`meeting-speaker-badge ${speakerClass(left.speaker)}`}
                  >
                    {left.speaker}
                  </span>
                  <p className="meeting-log-text">{left.text}</p>
                </>
              ) : (
                <p className="meeting-log-text meeting-empty">(없음)</p>
              )}
            </div>
            <div className="meeting-compare-col">
              <p className="meeting-compare-label">전체 {right ? i + 1 : ""}</p>
              {right ? (
                <>
                  <span
                    className={`meeting-speaker-badge ${speakerClass(right.speaker)}`}
                  >
                    {right.speaker}
                  </span>
                  <p className="meeting-log-text">{right.text}</p>
                </>
              ) : (
                <p className="meeting-log-text meeting-empty">(없음)</p>
              )}
            </div>
          </article>
        );
      })}
    </>
  );
}
