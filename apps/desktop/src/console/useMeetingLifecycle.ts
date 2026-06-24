// === ANCHOR: USE_MEETING_LIFECYCLE_START ===
import { useMemo, useState } from "react";
import { loadValues, storeValues } from "../setup/setupValues";
import { loadOperatorLogin } from "../setup/credentials";
import { startSidecar, stopSidecar } from "../setup/sidecarRunner";
import { createSession, endSession, fetchSessionReport, fetchSessionReportHtml, loginOperator, sessionRequestBody } from "./sessionApi";
import { runOneClickStart } from "./oneClickStart";
import { exportReports, exportSummary } from "./reportExport";
import type { CreatedSession, EndedSession, MeetingDraft } from "./types";

const initialDraft: MeetingDraft = {
  email: "admin@yeson.local",
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
  const [reportHtml, setReportHtml] = useState("");
  const [statusText, setStatusText] = useState("회의를 시작하면 viewer URL이 여기에 표시됩니다.");
  const [handoffText, setHandoffText] = useState("Setup Assistant는 아직 새 회의값을 받지 않았습니다.");
  const [errorText, setErrorText] = useState("");
  const [busy, setBusy] = useState(false);
  const [autoOpenExport, setAutoOpenExport] = useState(true);
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
      if (!draft.operatorToken) throw new Error("먼저 Operator email/password를 입력하고 Login operator를 눌러 로그인하세요.");
      if (!draft.title.trim()) throw new Error("Meeting title을 입력하세요.");
      const session = await createSession(draft);
      setCreatedSession(session);
      setEndedSession(null);
      setReportText("");
      setReportHtml("");
      storeSessionHandoff(session);
      setStatusText(`회의 생성 완료: ${session.session_id}`);
    });
  }

  async function startMeetingOneClick() {
    await runAction(async () => {
      const result = await runOneClickStart({
        loadOperatorLogin,
        login: async (email, password) => (await loginOperator(email, password)).access_token,
        createSession: ({ title, operatorToken }) => createSession({ ...draft, title, operatorToken }),
        startSidecar: async ({ serverWsBase, sessionId }) => {
          await startSidecar({ ...loadValues(), serverWsBase, sessionId, deviceApiKey: "" });
        },
        now: () => new Date(),
      });
      setCreatedSession(result.session);
      setEndedSession(null);
      setReportText("");
      setReportHtml("");
      updateDraft("operatorToken", result.operatorToken);
      updateDraft("title", result.title);
      storeSessionHandoff(result.session);
      if (result.sidecarStarted) {
        setStatusText(`회의 시작 완료: ${result.session.session_id}`);
      } else {
        setErrorText(
          `회의는 생성됐지만 sidecar 시작에 실패했습니다: ${result.sidecarError ?? ""} — 필요하면 '회의 종료'를 누르세요.`,
        );
      }
    });
  }

  async function finishMeeting() {
    await runAction(async () => {
      if (!createdSession) throw new Error("먼저 회의를 시작하세요.");
      const ended = await endSession(createdSession.session_id, draft.operatorToken);
      setEndedSession(ended);
      // Best-effort: the meeting is already ended server-side, so a sidecar that
      // is not running (or already exited) must not block the end UX. Errors are
      // logged inside sidecarRunner; swallow here so state still clears.
      try {
        await stopSidecar();
      } catch {
        // ignore — meeting end already succeeded
      }
      // Clear the active session so the one-click button reverts to "회의 시작".
      setCreatedSession(null);
      setStatusText(`회의 종료 완료: ${ended.ended_at}`);
    });
  }

  async function downloadReport() {
    await runAction(async () => {
      // After 회의 종료, createdSession is cleared (button reverts to 회의 시작) but
      // the report is still fetchable via the ended session's id.
      const sessionId = createdSession?.session_id ?? endedSession?.session_id;
      if (!sessionId) throw new Error("먼저 회의를 시작하세요.");
      setReportText(await fetchSessionReport(sessionId, draft.operatorToken));
      // HTML preview: best-effort — failure does not block md display.
      try {
        setReportHtml(await fetchSessionReportHtml(sessionId, draft.operatorToken));
      } catch {
        // ignore — md is already shown
      }
      setStatusText("Markdown 리포트를 불러왔습니다.");
    });
  }

  async function exportReport() {
    await runAction(async () => {
      const sessionId = createdSession?.session_id ?? endedSession?.session_id;
      if (!sessionId) throw new Error("먼저 회의를 시작하세요.");
      const result = await exportReports(sessionId, draft.operatorToken, undefined, {
        openFolder: autoOpenExport,
      });
      if (result.saved.length === 0 && result.skipped.length > 0) {
        const reasons = result.skipped.map((s) => `${s.fmt}: ${s.reason}`).join(", ");
        throw new Error(`보고서 저장 실패: ${reasons}`);
      }
      const skippedNote =
        result.skipped.length > 0 ? ` (스킵: ${result.skipped.map((s) => s.fmt).join(", ")})` : "";
      setStatusText(`보고서 저장 완료: ${result.saved.join(", ")}${skippedNote}`);
    });
  }

  async function exportSummaryReport() {
    await runAction(async () => {
      const sessionId = createdSession?.session_id ?? endedSession?.session_id;
      if (!sessionId) throw new Error("먼저 회의를 시작하세요.");
      const result = await exportSummary(sessionId, draft.operatorToken);
      if (!result.saved) {
        throw new Error(result.reason);
      }
      setStatusText(`요약본 저장 완료: ${result.path}`);
    });
  }

  async function copyViewerUrl() {
    if (!createdSession) return;
    await navigator.clipboard.writeText(createdSession.viewer_url);
    setStatusText("Viewer URL을 복사했습니다.");
  }

  return {
    autoOpenExport,
    busy,
    contractPreview,
    createdSession,
    draft,
    endedSession,
    errorText,
    handoffText,
    reportHtml,
    reportText,
    statusText,
    copyViewerUrl,
    downloadReport,
    exportReport,
    exportSummaryReport,
    finishMeeting,
    login,
    setAutoOpenExport,
    startMeeting,
    startMeetingOneClick,
    updateDraft,
  };

  function storeSessionHandoff(session: CreatedSession) {
    const current = loadValues();
    storeValues({
      ...current,
      sessionId: session.session_id,
      viewerUrl: session.viewer_url,
    });
    setHandoffText("Setup Assistant에 session ID와 viewer URL을 저장했습니다. Setup 탭에서 sidecar를 시작하세요.");
  }
}

function buildContractPreview(sessionPayload: ReturnType<typeof sessionRequestBody>): string {
  return [
    "POST /api/v1/sessions",
    "Authorization: Bearer <operator-token>",
    JSON.stringify(sessionPayload, null, 2),
    "",
    "POST /api/v1/sessions/{session_id}/end",
    "GET  /api/v1/sessions/{session_id}/report",
    "GET  /api/v1/sessions/{session_id}/utterances",
    "WS   /ws/operator?session={session_id}&access=<operator-token>",
  ].join("\n");
}
// === ANCHOR: USE_MEETING_LIFECYCLE_END ===
