// === ANCHOR: MEETING_QUICK_START_PANEL_START ===
import { LiveSubtitlePreview } from "../console/LiveSubtitlePreview";
import { useMeetingLifecycle } from "../console/useMeetingLifecycle";
import { styles } from "./styles";

export function MeetingQuickStartPanel() {
  const lifecycle = useMeetingLifecycle();
  const canStartMeeting = Boolean(lifecycle.draft.operatorToken) && !lifecycle.busy;
  const activeSessionId = lifecycle.createdSession?.session_id ?? null;

  return (
    <section style={styles.quickStartPanel}>
      <div style={styles.quickStartHeader}>
        <div>
          <p style={styles.eyebrow}>one-screen test flow</p>
          <h2 style={styles.quickStartTitle}>회의 생성부터 sidecar 실행까지 한 화면에서</h2>
          <p style={styles.quickStartIntro}>
            더 이상 Live Meeting 탭과 Setup Assistant를 왕복하지 않아도 됩니다. 먼저 로그인하고 회의를 만들면 Session ID와 Viewer URL이 아래 실행값에 자동 반영됩니다.
          </p>
        </div>
        <div style={styles.quickStartSteps}>
          <span>1 로그인</span>
          <span>2 회의 생성</span>
          <span>3 Sidecar 시작</span>
        </div>
      </div>

      <div style={styles.quickStartSubtitleDock}>
        <LiveSubtitlePreview operatorToken={lifecycle.draft.operatorToken} sessionId={activeSessionId} />
      </div>

      <div style={styles.quickStartGrid}>
        <div style={styles.quickStartCard}>
          <h3 style={styles.quickStartCardTitle}>회의 정보</h3>
          <div style={styles.fieldRow}>
            <QuickField label="Operator email" value={lifecycle.draft.email} type="email" onChange={(value) => lifecycle.updateDraft("email", value)} />
            <QuickField label="Operator password" value={lifecycle.draft.password} type="password" onChange={(value) => lifecycle.updateDraft("password", value)} />
          </div>
          <button type="button" onClick={lifecycle.login} disabled={lifecycle.busy} style={styles.secondaryLightButton}>
            {lifecycle.draft.operatorToken ? "Operator login 완료" : "Login operator"}
          </button>
          <div style={styles.fieldRow}>
            <QuickField label="Meeting title" value={lifecycle.draft.title} onChange={(value) => lifecycle.updateDraft("title", value)} />
            <QuickField label="Client label" value={lifecycle.draft.clientLabel} onChange={(value) => lifecycle.updateDraft("clientLabel", value)} />
          </div>
          <div style={styles.quickStartActions}>
            <button type="button" onClick={lifecycle.startMeeting} disabled={!canStartMeeting} style={{ ...styles.primaryButton, ...(!canStartMeeting ? styles.disabledButton : null) }}>
              {lifecycle.draft.operatorToken ? "회의 만들기" : "로그인 후 회의 만들기"}
            </button>
            <button type="button" onClick={lifecycle.finishMeeting} disabled={lifecycle.busy || !activeSessionId} style={{ ...styles.secondaryLightButton, ...(lifecycle.busy || !activeSessionId ? styles.disabledButton : null) }}>
              회의 종료
            </button>
          </div>
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
              <button type="button" onClick={lifecycle.copyViewerUrl} style={styles.secondaryButton}>
                Viewer URL 복사
              </button>
            </>
          ) : (
            <p style={styles.quickStartEmpty}>회의를 만들면 Session ID와 Viewer URL이 여기에 표시되고, 아래 sidecar 실행값에도 자동으로 채워집니다.</p>
          )}
        </div>
      </div>
    </section>
  );
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
