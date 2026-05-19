// === ANCHOR: CONSOLE_TYPES_START ===
export type ConsoleView = "setup" | "meet" | "history" | "settings";

export type MeetingDraft = {
  title: string;
  clientLabel: string;
  visibility: "org" | "private";
  operatorToken: string;
};
// === ANCHOR: CONSOLE_TYPES_END ===
