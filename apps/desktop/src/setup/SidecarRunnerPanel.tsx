// === ANCHOR: SIDECAR_RUNNER_PANEL_START ===
import { useEffect, useState } from "react";
import { loadSidecarStatus, startSidecar, stopSidecar, type SidecarStatus } from "./sidecarRunner";
import { styles } from "./styles";
import type { SetupValues } from "./types";

type SidecarRunnerPanelProps = {
  values: SetupValues;
};

const INITIAL_STATUS: SidecarStatus = {
  running: false,
  pid: null,
  detail: "sidecar 상태를 아직 확인하지 않았습니다.",
};

export function SidecarRunnerPanel({ values }: SidecarRunnerPanelProps) {
  const [status, setStatus] = useState<SidecarStatus>(INITIAL_STATUS);
  const [busy, setBusy] = useState(false);
  const [errorText, setErrorText] = useState("");
  const missingItems = sidecarMissingItems(values);
  const canStart = missingItems.length === 0;

  useEffect(() => {
    void refreshStatus();
  }, []);

  async function run(action: () => Promise<SidecarStatus>) {
    setBusy(true);
    setErrorText("");
    try {
      setStatus(await action());
    } catch (error) {
      setErrorText(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function refreshStatus() {
    await run(loadSidecarStatus);
  }

  return (
    <section style={styles.runnerPanel}>
      <div>
        <p style={styles.eyebrow}>sidecar runner</p>
        <h2 style={styles.sectionTitle}>3. 앱에서 sidecar 실행</h2>
        <p style={styles.runnerIntro}>
          패키지 Mac 앱은 번들된 네이티브 ScreenCaptureKit sidecar를 자동으로 실행합니다. 아래 버튼으로 sidecar를 시작/중지할 수 있습니다.
          Sidecar project folder가 설정된 경우에는 dev/fallback 경로(<code>uv</code> + <code>apps.client_sidecar</code>)를 사용합니다.
        </p>
      </div>

      <div style={styles.runnerActions}>
        <button type="button" disabled={busy || status.running || !canStart} onClick={() => run(() => startSidecar(values))} style={styles.primaryButton}>
          {status.running ? "Sidecar 실행 중" : "Sidecar 시작"}
        </button>
        <button type="button" disabled={busy || !status.running} onClick={() => run(stopSidecar)} style={styles.runnerStopButton}>
          Sidecar 중지
        </button>
        <button type="button" disabled={busy} onClick={refreshStatus} style={styles.runnerRefreshButton}>
          상태 새로고침
        </button>
      </div>

      <div style={styles.runnerStatus}>
        <strong>{status.running ? `실행 중${status.pid ? ` · PID ${status.pid}` : ""}` : "중지됨"}</strong>
        <span>전송 대상 Session ID: {values.sessionId || "아직 없음"}</span>
        <span>{status.detail}</span>
      </div>
      {missingItems.length > 0 ? (
        <ul style={styles.runnerChecklist}>
          {missingItems.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
      {errorText ? <p style={styles.runnerError}>{errorText}</p> : null}
    </section>
  );
}

function sidecarMissingItems(values: SetupValues): string[] {
  const items: string[] = [];
  if (!values.sessionId.trim() || values.sessionId.includes("<")) items.push("Live Meeting에서 회의를 만들고 Session ID를 채워야 합니다.");
  if (!values.serverWsBase.trim() || values.serverWsBase.includes("<")) items.push("WebSocket 서버 주소가 필요합니다.");
  return items;
}
// === ANCHOR: SIDECAR_RUNNER_PANEL_END ===
