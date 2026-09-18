/** Meeting Log Amazon Transcribe API (from meeting-log installer). */
export type MeetingLogConfig = {
  projectName: string;
  apiGatewayUrl: string;
  apiStreamUrl: string;
  apiUploadUrl: string;
  apiBatchUrl: string;
  apiBatchJobUrl: string;
};

const DEFAULT_API =
  (import.meta.env.VITE_MEETING_LOG_API as string | undefined)?.replace(/\/$/, "") ||
  "https://waeyt5xnx1.execute-api.us-west-2.amazonaws.com";

export const MEETING_LOG_CONFIG: MeetingLogConfig = {
  projectName: "meeting-log",
  apiGatewayUrl: DEFAULT_API,
  apiStreamUrl: `${DEFAULT_API}/stream-url`,
  apiUploadUrl: `${DEFAULT_API}/upload-url`,
  apiBatchUrl: `${DEFAULT_API}/batch-transcribe`,
  apiBatchJobUrl: `${DEFAULT_API}/batch-job`,
};

export const TARGET_SAMPLE_RATE = 16000;
export const SPEAKERS = ["A", "B", "C", "D", "E", "F"] as const;
export type SpeakerId = (typeof SPEAKERS)[number];

export const DEFAULT_MEETING_TITLE = "제목없음";
export const MEETING_FOLDER = "Meeting";
export const STORAGE_KEY = "ob-docs:meeting-log-entries-v1";
export const BATCH_STORAGE_KEY = "ob-docs:meeting-log-batch-v1";
