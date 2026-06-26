// === ANCHOR: MEETING_QUICK_START_PANEL_START ===
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { LiveSubtitlePreview } from "../console/LiveSubtitlePreview";
import { ViewerQrPanel } from "../console/ViewerQrPanel";
import { loginOperator, selfEnrollDevice, type ReportFormat } from "../console/sessionApi";
import { useMeetingLifecycle } from "../console/useMeetingLifecycle";
import { EMPTY_META, hydrateServerAddressFromKeychain, loadCredentialsMeta, saveCredentials, type CredentialsMeta } from "./credentials";
import { discoverServer, probeLocalServer, resolveServerWsBase } from "./serverDiscovery";
import { loadValues, storeValues } from "./setupValues";
import { styles } from "./styles";

export function MeetingQuickStartPanel() {
  const lifecycle = useMeetingLifecycle();
  const [meta, setMeta] = useState<CredentialsMeta>(EMPTY_META);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(() => ({
    email: "admin@yeson.local",
    password: "",
  }));
  const [serverWsBase, setServerWsBase] = useState<string>(() => loadValues().serverWsBase);
  const [discovering, setDiscovering] = useState(false);
  const [manualEntry, setManualEntry] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState("");
  const activeSessionId = lifecycle.createdSession?.session_id ?? null;
  const registered = meta.hasCredentials && !editing;

  const findServer = useCallback(async () => {
    setDiscovering(true);
    try {
      const resolved = await resolveServerWsBase({
        probeLocal: probeLocalServer,
        discover: discoverServer,
      });
      if (resolved) {
        setServerWsBase(resolved);
        setManualEntry(false);
      } else {
        setManualEntry(true);
      }
    } finally {
      setDiscovering(false);
    }
  }, []);

  useEffect(() => {
    void refreshMeta();
  }, []);

  useEffect(() => {
    if (!serverWsBase) void findServer();
  }, [serverWsBase, findServer]);

  async function refreshMeta() {
    const next = await loadCredentialsMeta();
    setMeta(next);
    setForm((current) => ({
      ...current,
      email: next.email || current.email,
    }));
    if (next.serverWsBase) setServerWsBase(next.serverWsBase);
  }

  async function registerAndStart() {
    setRegistering(true);
    setError("");
    try {
      // Point apiBase() at the chosen server via the localStorage cache only (no secret, no keychain write yet).
      storeValues({ ...loadValues(), serverWsBase });
      const { access_token: operatorToken } = await loginOperator(form.email, form.password);
      const deviceName = `client-${navigator.platform || "device"}`;
      const apiKey = await selfEnrollDevice(operatorToken, deviceName);
      await saveCredentials({ serverWsBase, email: form.email, password: form.password, deviceApiKey: apiKey });
      await hydrateServerAddressFromKeychain();
      await refreshMeta();
      setEditing(false);
      await lifecycle.startMeetingOneClick();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRegistering(false);
    }
  }

  return (
    <section style={styles.quickStartPanel}>
      <div style={styles.quickStartHeader}>
        <div>
          <p style={styles.eyebrow}>one-click meeting start</p>
          <h2 style={styles.quickStartTitle}>버튼 하나로 회의 시작</h2>
          <p style={styles.quickStartIntro}>
            처음 한 번만 로그인 정보를 등록하면, 이후에는 버튼 하나로 회의가 시작되고 참가자 자막 송출까지 자동으로 이어집니다.
          </p>
        </div>
        <div style={styles.quickStartSteps}>
          <span>로그인</span>
          <span>회의 시작</span>
          <span>자막 송출</span>
        </div>
      </div>

      <div style={styles.quickStartSubtitleDock}>
        <LiveSubtitlePreview
          operatorToken={lifecycle.draft.operatorToken}
          sessionId={activeSessionId}
          meetingStartLabel={lifecycle.meetingStartedAt ? formatLocalTime(lifecycle.meetingStartedAt) : undefined}
          meetingEndLabel={endTimeLabel(lifecycle.endedSession?.ended_at, activeSessionId)}
        />
      </div>

      <div style={styles.quickStartGrid}>
        <div style={styles.quickStartCard}>
          {registered ? (
            <>
              <h3 style={styles.quickStartCardTitle}>준비 완료</h3>
              {activeSessionId ? (
                <button type="button" onClick={lifecycle.finishMeeting} disabled={lifecycle.busy} style={styles.primaryButton}>
                  회의 종료
                </button>
              ) : (
                <button type="button" onClick={lifecycle.startMeetingOneClick} disabled={lifecycle.busy} style={styles.primaryButton}>
                  {lifecycle.busy ? "회의 시작 중..." : "회의 시작"}
                </button>
              )}
              <div style={styles.quickStartSessionBox}>
                <span>서버</span>
                <strong>{meta.serverWsBase || "(미설정)"}</strong>
              </div>
              <div style={styles.quickStartSessionBox}>
                <span>운영자</span>
                <strong>{meta.email || "(미설정)"}</strong>
              </div>
              <div style={styles.quickStartSessionBox}>
                <span>Device Key</span>
                <strong>{meta.hasDeviceKey ? "저장됨 ✓" : "없음"}</strong>
              </div>
              <button type="button" onClick={() => setEditing(true)} style={styles.secondaryLightButton}>
                자격증명 변경
              </button>
            </>
          ) : (
            <>
              <h3 style={styles.quickStartCardTitle}>처음 한 번만 등록</h3>
              <div style={discoveryRowStyle}>
                <span style={styles.label}>서버 주소</span>
                <code style={discoveryValueStyle}>{serverWsBase || "찾는 중..."}</code>
                <button type="button" onClick={() => void findServer()} disabled={discovering}>
                  {discovering ? "찾는 중..." : "다시 찾기"}
                </button>
              </div>
              {manualEntry && (
                <QuickField
                  label="서버 주소 (직접 입력)"
                  value={serverWsBase}
                  onChange={(value) => setServerWsBase(value)}
                />
              )}
              <QuickField label="Operator email" value={form.email} type="email" onChange={(value) => setForm((c) => ({ ...c, email: value }))} />
              <QuickField label="Operator password" value={form.password} type="password" onChange={(value) => setForm((c) => ({ ...c, password: value }))} /> {/* vibelign: allow-secret — field name only, not a key value */}
              <div style={styles.quickStartActions}>
                <button type="button" onClick={registerAndStart} disabled={registering || lifecycle.busy} style={styles.primaryButton}>
                  {registering ? "등록 중..." : "기억하고 회의 시작"}
                </button>
                {meta.hasCredentials ? (
                  <button type="button" onClick={() => setEditing(false)} disabled={lifecycle.busy} style={styles.secondaryLightButton}>
                    취소
                  </button>
                ) : null}
              </div>
              {error && <p style={{ color: "#f87171", fontSize: 12, marginTop: 6 }}>{error}</p>}
            </>
          )}
          <div style={lifecycle.errorText ? styles.quickStartError : styles.quickStartStatus}>
            {lifecycle.errorText || lifecycle.statusText}
          </div>
        </div>

        <div style={styles.quickStartCardDark}>
          <h3 style={styles.quickStartCardTitleDark}>생성된 회의</h3>
          {lifecycle.createdSession ? (
            <>
              <div style={styles.quickStartSessionBox}>
                <span>Session ID</span>
                <strong>{lifecycle.createdSession.session_id}</strong>
              </div>
              <div style={styles.quickStartSessionBox}>
                <span>Viewer URL</span>
                <strong>{lifecycle.createdSession.viewer_url}</strong>
              </div>
              {lifecycle.createdSession.viewer_url ? (
                <ViewerQrPanel viewerUrl={lifecycle.createdSession.viewer_url} />
              ) : null}
              <button type="button" onClick={lifecycle.copyViewerUrl} style={styles.secondaryButton}>
                Viewer URL 복사
              </button>
            </>
          ) : lifecycle.endedSession ? (
            <>
              <div style={styles.quickStartSessionBox}>
                <span>회의록</span>
                <strong>{lifecycle.endedSession.report_path}</strong>
              </div>
              <button type="button" onClick={lifecycle.downloadReport} disabled={lifecycle.busy} style={styles.secondaryButton}>
                {lifecycle.busy ? "불러오는 중..." : "보고서 미리보기 불러오기"}
              </button>
              {lifecycle.reportHtml ? (
                <iframe sandbox="" srcDoc={lifecycle.reportHtml} title="보고서 미리보기" style={reportPreviewStyle} />
              ) : lifecycle.reportText ? (
                <pre style={reportTextStyle}>{lifecycle.reportText}</pre>
              ) : null}
              <div style={formatChecklistStyle}>
                <span style={formatChecklistLabelStyle}>저장 포맷</span>
                <div style={formatChecklistRowStyle}>
                  {EXPORT_FORMAT_OPTIONS.map((fmt) => (
                    <label key={fmt} style={autoOpenLabelStyle}>
                      <input
                        type="checkbox"
                        checked={lifecycle.selectedFormats[fmt]}
                        onChange={() => lifecycle.toggleFormat(fmt)}
                        style={{ accentColor: "#38bdf8" }}
                      />
                      {fmt.toUpperCase()}
                    </label>
                  ))}
                </div>
              </div>
              <button type="button" onClick={lifecycle.exportReport} disabled={lifecycle.busy} style={styles.secondaryButton}>
                보고서 익스포트
              </button>
              <button type="button" onClick={lifecycle.exportSummaryReport} disabled={lifecycle.busy} style={styles.secondaryButton}>
                요약본 저장
              </button>
              <label style={autoOpenLabelStyle}>
                <input
                  type="checkbox"
                  checked={lifecycle.autoOpenExport}
                  onChange={(e) => lifecycle.setAutoOpenExport(e.target.checked)}
                  style={{ accentColor: "#38bdf8" }}
                />
                익스포트 후 폴더 자동 열기
              </label>
            </>
          ) : (
            <p style={styles.quickStartEmpty}>회의를 시작하면 Session ID와 Viewer URL이 여기에 표시됩니다.</p>
          )}
        </div>
      </div>
    </section>
  );
}

const discoveryRowStyle: CSSProperties = {
  display: "flex",
  gap: 10,
  alignItems: "center",
  marginBottom: 10,
};

const discoveryValueStyle: CSSProperties = {
  fontWeight: 700,
  color: "#1e3a8a",
  flex: 1,
  overflowWrap: "anywhere",
};

const reportTextStyle: CSSProperties = {
  marginTop: 10,
  maxHeight: 220,
  overflow: "auto",
  whiteSpace: "pre-wrap",
  fontSize: 12,
  lineHeight: 1.5,
  background: "rgba(255,255,255,0.08)",
  padding: 10,
  borderRadius: 8,
};

const reportPreviewStyle: CSSProperties = {
  marginTop: 10,
  width: "100%",
  height: 320,
  border: "1px solid rgba(255,255,255,0.12)",
  borderRadius: 8,
  background: "#fff",
};

const autoOpenLabelStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginTop: 8,
  fontSize: 13,
  color: "#94a3b8",
  cursor: "pointer",
};

const EXPORT_FORMAT_OPTIONS: ReportFormat[] = ["md", "html", "docx", "pdf"];

const formatChecklistStyle: CSSProperties = {
  marginTop: 10,
};

const formatChecklistLabelStyle: CSSProperties = {
  display: "block",
  fontSize: 12,
  color: "#94a3b8",
  marginBottom: 4,
};

const formatChecklistRowStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 16,
};

function formatLocalTime(value: Date | string | null | undefined): string {
  if (!value) return "-";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function endTimeLabel(endedAt: string | null | undefined, activeSessionId: string | null): string {
  if (endedAt) return formatLocalTime(endedAt);
  if (activeSessionId) return "진행중";
  return "-";
}

function QuickField({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
}) {
  return (
    <label style={styles.field}>
      <span style={styles.label}>{label}</span>
      <input type={type} value={value} onChange={(event) => onChange(event.currentTarget.value)} style={styles.input} />
    </label>
  );
}
// === ANCHOR: MEETING_QUICK_START_PANEL_END ===
