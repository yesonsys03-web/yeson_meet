// === ANCHOR: CONSOLE_TYPES_START ===
export type ConsoleView = "setup" | "meet" | "history" | "settings";

export type MeetingDraft = {
  email: string;
  password: string;
  title: string;
  clientLabel: string;
  visibility: "org" | "private";
  operatorToken: string;
};

export type TokenPair = {
  access_token: string;
  refresh_token: string;
};

export type CreatedSession = {
  session_id: string;
  viewer_url: string;
};

export type EndedSession = {
  session_id: string;
  status: string;
  ended_at: string;
  report_path: string;
};

export type UtteranceTranscribed = {
  type: "utterance.transcribed";
  session_id: string;
  occurred_at: string;
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
  session_id: string;
  occurred_at: string;
  ended_at: string;
};

export type DomainEvent = UtteranceTranscribed | SessionEnded;
// === ANCHOR: CONSOLE_TYPES_END ===
