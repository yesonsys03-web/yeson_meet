// === ANCHOR: ONE_CLICK_START_START ===
import { formatMeetingTitle } from "./meetingTitle";
import type { CreatedSession } from "./types";

export type OneClickDeps = {
  loadOperatorLogin: () => Promise<{ serverWsBase: string; email: string; password: string }>;
  login: (email: string, password: string) => Promise<string>;
  createSession: (input: { title: string; operatorToken: string }) => Promise<CreatedSession>;
  startSidecar: (input: { serverWsBase: string; sessionId: string }) => Promise<void>;
  now: () => Date;
};

export type OneClickResult = {
  session: CreatedSession;
  operatorToken: string;
  title: string;
  sidecarStarted: boolean;
  sidecarError?: string;
};

/// Run the everyday one-click sequence. Login/create failures propagate (no
/// session is created). A sidecar failure is captured, not thrown, so the
/// created session survives and the caller can offer "회의 종료".
export async function runOneClickStart(deps: OneClickDeps): Promise<OneClickResult> {
  const { serverWsBase, email, password } = await deps.loadOperatorLogin();
  const operatorToken = await deps.login(email, password);
  const title = formatMeetingTitle(deps.now());
  const session = await deps.createSession({ title, operatorToken });

  try {
    await deps.startSidecar({ serverWsBase, sessionId: session.session_id });
    return { session, operatorToken, title, sidecarStarted: true };
  } catch (error) {
    return {
      session,
      operatorToken,
      title,
      sidecarStarted: false,
      sidecarError: error instanceof Error ? error.message : String(error),
    };
  }
}
// === ANCHOR: ONE_CLICK_START_END ===
