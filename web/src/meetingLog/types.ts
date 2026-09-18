import type { SpeakerId } from "./config";

export type MeetingEntry = {
  id: string;
  speaker: SpeakerId | string;
  text: string;
  at: number;
  start?: number;
};

export type BatchEntry = {
  speaker: string;
  text: string;
  start?: number;
};

export type MeetingView = "live" | "batch" | "compare";

export type InterimState = {
  speaker: string;
  text: string;
} | null;
