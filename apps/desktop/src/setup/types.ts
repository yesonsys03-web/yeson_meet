// === ANCHOR: TYPES_START ===
export type SmokeStatus = "idle" | "checking" | "ok" | "fail";

export type SmokeCheckKey = "server" | "gemini" | "viewer";

export type SmokeCheck = {
  key: SmokeCheckKey;
  label: string;
  description: string;
  status: SmokeStatus;
  detail: string;
};

export type SetupPlatform = "windows" | "mac";

export type SetupValues = {
  platform: SetupPlatform;
  serverWsBase: string;
  deviceApiKey: string;
  sessionId: string;
  viewerUrl: string;
  audioDeviceName: string;
};
// === ANCHOR: TYPES_END ===
