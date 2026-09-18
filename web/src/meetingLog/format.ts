import type { BatchEntry, MeetingEntry } from "./types";

export function formatTime(ts: number): string {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(ts));
}

export function formatOffset(seconds?: number): string {
  if (seconds == null || Number.isNaN(Number(seconds))) return "";
  const total = Math.max(0, Number(seconds));
  const m = Math.floor(total / 60);
  const s = Math.floor(total % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function speakerClass(speaker: string): string {
  return `speaker-${String(speaker || "A").toLowerCase()}`;
}

/** Extract a meeting title from transcript text (first utterance / opening phrase). */
export function extractTitleFromEntries(
  entries: Array<Pick<MeetingEntry, "text"> | Pick<BatchEntry, "text">>,
): string {
  const parts = entries
    .map((e) => String(e.text || "").trim())
    .filter(Boolean);
  if (!parts.length) return "회의록";

  // Prefer the first utterance; if very short, append the next one.
  let raw = parts[0]!;
  if (raw.length < 12 && parts[1]) {
    raw = `${raw} ${parts[1]}`;
  }

  raw = raw.replace(/\s+/g, " ").trim();
  // Drop trailing sentence punctuation only (keep commas mid-title)
  raw = raw.replace(/[.!?。！？]+$/u, "").trim();
  return raw || "회의록";
}

export function sanitizeMeetingFilename(title: string): string {
  const cleaned = title
    .replace(/[\\/:*?"<>|#]/g, "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);
  return cleaned || "회의록";
}

/** Filename stem: `YYYY-MM-DD 제목` */
export function meetingFileBaseName(title: string, recordedAt: Date): string {
  const date = formatMeetingDate(recordedAt);
  const safeTitle = sanitizeMeetingFilename(title);
  return `${date} ${safeTitle}`;
}

export function formatClockTime(ts: number | Date): string {
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(ts instanceof Date ? ts : new Date(ts));
}

export function entryWallClock(
  entry: { start?: number; at?: number },
  recordedAt: Date,
): Date {
  if (entry.at != null && Number.isFinite(entry.at)) {
    return new Date(entry.at);
  }
  if (entry.start != null && Number.isFinite(entry.start)) {
    return new Date(recordedAt.getTime() + Math.max(0, entry.start) * 1000);
  }
  return recordedAt;
}

export function formatMeetingDate(d = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function buildMeetingMarkdown(
  title: string,
  entries: Array<{ speaker: string; text: string; start?: number; at?: number }>,
  recordedAt: Date,
): string {
  const date = formatMeetingDate(recordedAt);
  const time = formatClockTime(recordedAt);
  const heading = meetingFileBaseName(title, recordedAt);

  // Blank line between turns so Markdown keeps each on its own paragraph
  const body = entries
    .map((e) => {
      const clock = formatClockTime(entryWallClock(e, recordedAt));
      const speaker = `참석자 ${e.speaker || "A"}`;
      return `${clock} ${speaker}: ${e.text}`;
    })
    .join("\n\n");

  return [
    `# ${heading}`,
    "",
    `- 날짜: ${date} ${time}`,
    `- 화자 수: ${new Set(entries.map((e) => e.speaker)).size}`,
    "",
    "## 회의 내용",
    "",
    body,
    "",
  ].join("\n");
}

export function entriesToCopyText(
  view: "live" | "batch" | "compare",
  live: MeetingEntry[],
  batch: BatchEntry[],
): string {
  if (view === "batch") {
    return batch.map((item) => `[${item.speaker}] ${item.text}`).join("\n");
  }
  if (view === "compare") {
    const n = Math.max(live.length, batch.length);
    const lines = ["# 실시간\t전체"];
    for (let i = 0; i < n; i += 1) {
      const left = live[i];
      const right = batch[i];
      lines.push(
        `${left ? `[${left.speaker}] ${left.text}` : "(없음)"}\t${
          right ? `[${right.speaker}] ${right.text}` : "(없음)"
        }`,
      );
    }
    return lines.join("\n");
  }
  return live.map((item) => `[${item.speaker}] ${item.text}`).join("\n");
}
