// === ANCHOR: USE_MEETING_LIFECYCLE_START ===
import { useEffect, useMemo, useRef, useState } from "react";
import { loadValues, storeValues } from "../setup/setupValues";
import { loadOperatorLogin } from "../setup/credentials";
import { startSidecar, stopSidecar } from "../setup/sidecarRunner";
import { createSession, endSession, fetchSessionReport, fetchSessionReportHtml, fetchSessionViewerUrl, loginOperator, sessionRequestBody, type ReportFormat } from "./sessionApi";
import { runOneClickStart } from "./oneClickStart";
import { DEFAULT_EXPORT_FORMATS, exportReports, exportSummary } from "./reportExport";
import type { CreatedSession, EndedSession, MeetingDraft } from "./types";

// While a meeting is live, re-check the viewer link this often. The server
// console can re-publish the public tunnel mid-meeting (tunnel drop recovery),
// which mints a NEW host — the QR must follow. Cheap LAN GET; 10s keeps the QR
// close behind the ~2-15s auto re-publish without polling chatter.
const VIEWER_URL_REFRESH_MS = 10_000;

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
  const [handoffText, setHandoffText] = useState("미팅 시작 탭은 아직 새 회의값을 받지 않았습니다.");
  const [errorText, setErrorText] = useState("");
  const [busy, setBusy] = useState(false);
  const [autoOpenExport, setAutoOpenExport] = useState(true);
  const [meetingStartedAt, setMeetingStartedAt] = useState<Date | null>(null);
  const [selectedFormats, setSelectedFormats] = useState<Record<ReportFormat, boolean>>(() =>
    DEFAULT_EXPORT_FORMATS.reduce(
      (acc, fmt) => ({ ...acc, [fmt]: true }),
      {} as Record<ReportFormat, boolean>,
    ),
  );
  const sessionPayload = useMemo(() => sessionRequestBody(draft), [draft]);
  const contractPreview = useMemo(() => buildContractPreview(sessionPayload), [sessionPayload]);

  // Viewer-link self-heal: while a meeting is live, keep the viewer URL (and
  // the QR rendered from it) in sync with the server's CURRENT viewer base.
  // Best-effort — a failed poll changes nothing and retries next tick. The ref
  // mirrors the latest URL so the interval closure compares fresh state without
  // re-arming on every URL change.
  const liveSessionId = createdSession?.session_id ?? null;
  const viewerUrlRef = useRef<string | null>(null);
  viewerUrlRef.current = createdSession?.viewer_url ?? null;
  useEffect(() => {
    if (!liveSessionId || !draft.operatorToken) return;
    const operatorToken = draft.operatorToken; // vibelign: allow-secret — 세션 상태의 토큰 참조일 뿐, 리터럴 아님
    const id = window.setInterval(() => {
      void (async () => {
        try {
          const next = await fetchSessionViewerUrl(liveSessionId, operatorToken);
          if (next === viewerUrlRef.current) return;
          setCreatedSession((current) =>
            current && current.session_id === liveSessionId
              ? { ...current, viewer_url: next }
              : current,
          );
          // Keep the 미팅 시작 tab handoff in sync too, then tell the operator
          // the old QR is dead and this one must be re-shared.
          storeValues({ ...loadValues(), sessionId: liveSessionId, viewerUrl: next });
          setStatusText("공개 링크가 새로 발급되었습니다 — 시청자에게 QR을 다시 공유하세요.");
        } catch {
          // best-effort: server momentarily unreachable / token expired — retry next tick
        }
      })();
    }, VIEWER_URL_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [liveSessionId, draft.operatorToken]);

  function updateDraft<K extends keyof MeetingDraft>(key: K, value: MeetingDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function toggleFormat(fmt: ReportFormat) {
    setSelectedFormats((current) => ({ ...current, [fmt]: !current[fmt] }));
  }

  function pickedFormats(): ReportFormat[] {
    return DEFAULT_EXPORT_FORMATS.filter((fmt) => selectedFormats[fmt]);
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
      setMeetingStartedAt(new Date());
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
      setMeetingStartedAt(new Date());
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
      // ended_at is UTC-aware ISO (server serializer); localize it so the time
      // matches the operator's wall clock instead of showing raw UTC.
      setStatusText(`회의 종료 완료: ${new Date(ended.ended_at).toLocaleString("ko-KR")}`);
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
      const fmts = pickedFormats();
      if (fmts.length === 0) throw new Error("포맷을 1개 이상 선택하세요.");
      const result = await exportReports(sessionId, draft.operatorToken, fmts, {
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
      const fmts = pickedFormats();
      if (fmts.length === 0) throw new Error("포맷을 1개 이상 선택하세요.");
      const result = await exportSummary(sessionId, draft.operatorToken, fmts, {
        openFolder: autoOpenExport,
      });
      if (result.saved.length === 0 && result.skipped.length > 0) {
        const reasons = result.skipped.map((s) => `${s.fmt}: ${s.reason}`).join(", ");
        throw new Error(`요약본 저장 실패: ${reasons}`);
      }
      const skippedNote =
        result.skipped.length > 0 ? ` (스킵: ${result.skipped.map((s) => s.fmt).join(", ")})` : "";
      setStatusText(`요약본 저장 완료: ${result.saved.join(", ")}${skippedNote}`);
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
    meetingStartedAt,
    reportHtml,
    reportText,
    selectedFormats,
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
    toggleFormat,
    updateDraft,
  };

  function storeSessionHandoff(session: CreatedSession) {
    const current = loadValues();
    storeValues({
      ...current,
      sessionId: session.session_id,
      viewerUrl: session.viewer_url,
    });
    setHandoffText("미팅 시작 탭에 session ID와 viewer URL을 저장했습니다. 미팅 시작 탭에서 sidecar를 시작하세요.");
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
