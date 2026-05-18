// === ANCHOR: EVENTS_START ===
// Mirror of apps/server/domain/events.py — Slice 1 locked schema.
// SSOT lives in Python; update both files in the same commit when shape changes.

export const EVENT_VERSION = "1" as const;

export type UtteranceTranscribed = {
  type: "utterance.transcribed";
  session_id: string; // UUID
  occurred_at: string; // ISO datetime
  seq: number;
  speaker: string | null;
  text_en: string;
  text_ko: string;
  started_at: string;
  ended_at: string;
  is_final: boolean;
};

export type SessionEnded = {
  type: "session.ended";
  session_id: string; // UUID
  occurred_at: string; // ISO datetime
  ended_at: string; // ISO datetime
};

export type DomainEvent = UtteranceTranscribed | SessionEnded;
// === ANCHOR: EVENTS_END ===
