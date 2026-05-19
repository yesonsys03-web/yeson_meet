// === ANCHOR: USE_MEETING_LIFECYCLE_START ===
import { useMemo, useState } from "react";
import { createSession, endSession, fetchSessionReport, loginOperator, sessionRequestBody } from "./sessionApi";
import type { CreatedSession, EndedSession, MeetingDraft } from "./types";

const initialDraft: MeetingDraft = {
  email: "admin@example.com",
  password: "",
  title: "Client weekly sync",
  clientLabel: "CLIENT-A",
  visibility: "org",
  operatorToken: "",
};

export function useMeetingLifecycle() {
  const [draft, setDraft] = useState<MeetingDraft>(initialDraft);
  const [createdSession, setCreatedSession] = useState<CreatedSession | null>(null);
  const [endedSession, setEndedSession] = useState<EndedSession | null>(null);
  const [reportText, setReportText] = useState("");
  const [statusText, setStatusText] = useState("회의를 시작하면 viewer URL이 여기에 표시됩니다.");
  const [errorText, setErrorText] = useState("");
  const [busy, setBusy] = useState(false);
  const sessionPayload = useMemo(() => sessionRequestBody(draft), [draft]);
  const contractPreview = useMemo(() => buildContractPreview(sessionPayload), [sessionPayload]);

  function updateDraft<K extends keyof MeetingDraft>(key: K, value: MeetingDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  async function runAction(action: () => Promise<void>) {
    setBusy(true);
    setErrorText("");
    try {
      await action();
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function login() {
    await runAction(async () => {
      if (!draft.email.trim()) throw new Error("Operator email을 입력하세요.");
      if (!draft.password) throw new Error("Operator password를 입력하세요.");
      const tokenPair = await loginOperator(draft.email, draft.password);
      updateDraft("operatorToken", tokenPair.access_token);
      updateDraft("password", "");
      setStatusText("Operator login 완료. 이제 회의를 시작할 수 있습니다.");
    });
  }

  async function startMeeting() {
    await runAction(async () => {
      if (!draft.operatorToken) throw new Error("Operator bearer token을 먼저 입력하세요.");
      if (!draft.title.trim()) throw new Error("Meeting title을 입력하세요.");
      const session = await createSession(draft);
      setCreatedSession(session);
      setEndedSession(null);
      setReportText("");
      setStatusText(`회의 생성 완료: ${session.session_id}`);
    });
  }

  async function finishMeeting() {
    await runAction(async () => {
      if (!createdSession) throw new Error("먼저 회의를 시작하세요.");
      const ended = await endSession(createdSession.session_id, draft.operatorToken);
      setEndedSession(ended);
      setStatusText(`회의 종료 완료: ${ended.ended_at}`);
    });
  }

  async function downloadReport() {
    await runAction(async () => {
      if (!createdSession) throw new Error("먼저 회의를 시작하세요.");
      setReportText(await fetchSessionReport(createdSession.session_id, draft.operatorToken));
      setStatusText("Markdown 리포트를 불러왔습니다.");
    });
  }

  async function copyViewerUrl() {
    if (!createdSession) return;
    await navigator.clipboard.writeText(createdSession.viewer_url);
    setStatusText("Viewer URL을 복사했습니다.");
  }

  return {
    busy,
    contractPreview,
    createdSession,
    draft,
    endedSession,
    errorText,
    reportText,
    statusText,
    copyViewerUrl,
    downloadReport,
    finishMeeting,
    login,
    startMeeting,
    updateDraft,
  };
}

function buildContractPreview(sessionPayload: ReturnType<typeof sessionRequestBody>): string {
  return [
    "POST /api/v1/sessions",
    "Authorization: Bearer <operator-token>",
    JSON.stringify(sessionPayload, null, 2),
    "",
    "POST /api/v1/sessions/{session_id}/end",
    "GET  /api/v1/sessions/{session_id}/report",
  ].join("\n");
}
// === ANCHOR: USE_MEETING_LIFECYCLE_END ===
