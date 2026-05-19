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
// === ANCHOR: CONSOLE_TYPES_END ===
