// === ANCHOR: SETUPASSISTANT_START ===
import { useEffect, useMemo, useState } from "react";
import { loadCredentialsMeta, updateServerWsBase as updateServerWsBaseKeychain } from "./credentials";
import { Field } from "./Field";
import { MeetingQuickStartPanel } from "./MeetingQuickStartPanel";
import { PlatformRunbookPanel } from "./PlatformRunbookPanel";
import { PlatformSelector } from "./PlatformSelector";
import { SidecarRunnerPanel } from "./SidecarRunnerPanel";
import { SmokeChecklist } from "./SmokeChecklist";
import { PLATFORM_CONFIG } from "./platformConfig";
import { buildSidecarCommand } from "./sidecarCommands";
import { loadValues, SETUP_VALUES_UPDATED_EVENT, storeValues } from "./setupValues";
import { initialSmokeChecks, runSmokeCheck, SMOKE_CHECK_ORDER } from "./smokeChecks";
import { styles } from "./styles";
import type { SetupPlatform, SetupValues, SmokeCheckKey, SmokeStatus } from "./types";

// === ANCHOR: SETUPASSISTANT_SETUPASSISTANT_START ===
export function SetupAssistant() {
  const [values, setValues] = useState<SetupValues>(loadValues);
  const [copied, setCopied] = useState(false);
  const [runningChecks, setRunningChecks] = useState(false);
  const [checks, setChecks] = useState(initialSmokeChecks);
  // === ANCHOR: SETUPASSISTANT_DEVICEKEY_START ===
  // hasCredentials gates the keychain write-through for the advanced server-address
  // field (updateServerWsBase). Device-key MINTING was removed from this operator
  // client — keys are issued in the SERVER console and pasted in (QuickStart
  // register flow / the manual field below).
  const [hasCredentials, setHasCredentials] = useState(false);

  useEffect(() => {
    loadCredentialsMeta()
      .then((meta) => setHasCredentials(meta.hasCredentials))
      .catch(() => undefined);
  }, []);
  // === ANCHOR: SETUPASSISTANT_DEVICEKEY_END ===

  const platformConfig = PLATFORM_CONFIG[values.platform];
  const sidecarCommand = useMemo(() => buildSidecarCommand(values), [values]);

  useEffect(() => {
    function syncStoredValues() {
      setValues((current) => ({
        ...loadValues(),
        deviceApiKey: current.deviceApiKey, // vibelign: allow-secret — field name only, not a key value
      }));
    }

    window.addEventListener(SETUP_VALUES_UPDATED_EVENT, syncStoredValues);
    return () => window.removeEventListener(SETUP_VALUES_UPDATED_EVENT, syncStoredValues);
  }, []);

  // === ANCHOR: SETUPASSISTANT_UPDATEVALUE_START ===
  function updateValue<K extends keyof SetupValues>(key: K, value: SetupValues[K]) {
    setValues((current) => {
      const next = { ...current, [key]: value };
      storeValues(next);
      return next;
    });
  }

  // P2: the keychain is the authored source of serverWsBase. When the user edits the
  // advanced manual field, write THROUGH to the keychain so the next hydrate cannot
  // silently overwrite the manual edit. We use the partial-merge command
  // (update_server_ws_base) which updates ONLY the address and preserves the stored
  // Device API Key, so this is now safe UNCONDITIONALLY once credentials exist — no
  // more !hasDeviceKey gate that dropped post-key edits. localStorage is written first
  // (derived cache) and kept even if the keychain write is best-effort.
  function updateServerWsBase(value: string) {
    updateValue("serverWsBase", value);
    if (!hasCredentials) return;
    void (async () => {
      try {
        await updateServerWsBaseKeychain(value);
      } catch {
        // keychain write-through is best-effort; localStorage already holds the edit.
      }
    })();
  }
  // === ANCHOR: SETUPASSISTANT_UPDATEVALUE_END ===

  // === ANCHOR: SETUPASSISTANT_UPDATEPLATFORM_START ===
  function updatePlatform(platform: SetupPlatform) {
    setValues((current) => {
      const next = {
        ...current,
        platform,
      };
      storeValues(next);
      return next;
    });
  }
  // === ANCHOR: SETUPASSISTANT_UPDATEPLATFORM_END ===

  // === ANCHOR: SETUPASSISTANT_COPYCOMMAND_START ===
  async function copyCommand() {
    await navigator.clipboard.writeText(sidecarCommand);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  }
  // === ANCHOR: SETUPASSISTANT_COPYCOMMAND_END ===

  // === ANCHOR: SETUPASSISTANT_RUNALLSMOKECHECKS_START ===
  async function runAllSmokeChecks() {
    setRunningChecks(true);
    for (const key of SMOKE_CHECK_ORDER) {
      markCheck(key, "checking", "확인하는 중입니다...");
      try {
        const result = await runSmokeCheck(key, values);
        markCheck(key, result.status, result.detail);
      } catch (error) {
        markCheck(key, "fail", error instanceof Error ? error.message : String(error));
      }
    }
    setRunningChecks(false);
  }
  // === ANCHOR: SETUPASSISTANT_RUNALLSMOKECHECKS_END ===

  // === ANCHOR: SETUPASSISTANT_MARKCHECK_START ===
  function markCheck(key: SmokeCheckKey, status: SmokeStatus, detail: string) {
    setChecks((current) => ({
      ...current,
      [key]: {
        ...current[key],
        status,
        detail,
      },
    }));
  }
  // === ANCHOR: SETUPASSISTANT_MARKCHECK_END ===

  return (
    <div style={styles.page}>
      <section style={styles.hero}>
        <div>
          <p style={styles.eyebrow}>yeson-meet</p>
          <h1 style={styles.title}>미팅 실시간 자막</h1>
          <p style={styles.subtitle}>번역과 보고서 작성</p>
        </div>
        <div style={styles.statusCard}>
          <span style={styles.statusLabel}>이 화면에서 하는 일</span>
          <strong>실시간 자막 · 번역 · 보고서</strong>
          <small>회의를 시작하면 참가자에게 번역 자막이 송출되고, 종료하면 보고서가 자동으로 만들어집니다.</small>
        </div>
      </section>

      <div style={styles.scrollBody}>
      <MeetingQuickStartPanel />

      <details style={{ marginTop: 24 }}>
        <summary style={styles.sectionTitle}>고급 설정 (수동 실행 · 문제 해결)</summary>

        <main style={styles.grid}>
          <section style={styles.panel}>
            <h2 style={styles.sectionTitle}>실행 환경 값</h2>
            <PlatformSelector value={values.platform} onChange={updatePlatform} />
            <Field
              label="WebSocket 서버 주소"
              help="로컬 테스트는 ws://127.0.0.1:8000, LAN HTTPS 테스트는 wss://<server-ip>:8000 처럼 입력합니다."
              value={values.serverWsBase}
              onChange={(value) => updateServerWsBase(value)}
            />
            <Field
              label="테스트용 오디오 키 (Device API Key)"
              help="서버 콘솔의 Devices에서 발급한 키를 붙여넣으세요(키 발급은 서버에서만 합니다). sidecar가 서버 접속에 사용합니다. 이 필드 값은 저장하지 않으므로 Sidecar 시작 직전에 붙여넣어야 합니다."
              value={values.deviceApiKey}
              secret
              onChange={(value) => updateValue("deviceApiKey", value)}
            />
            <Field
              label="Session ID"
              help="회의를 만들면 자동으로 채워집니다. 필요할 때만 직접 수정하세요."
              value={values.sessionId}
              onChange={(value) => updateValue("sessionId", value)}
            />
            <Field
              label="Viewer URL"
              help="회의를 만들면 자동으로 채워집니다. 폰이나 노트북에서 자막을 확인할 주소입니다."
              value={values.viewerUrl}
              onChange={(value) => updateValue("viewerUrl", value)}
            />
            <Field
              label="Sidecar project folder"
              help="dev에서 소스로 sidecar를 실행할 때만 필요합니다. 비워두면 패키지 앱은 번들된 네이티브 sidecar를 사용합니다."
              value={values.sidecarProjectDir}
              onChange={(value) => updateValue("sidecarProjectDir", value)}
            />
          </section>

          <section style={styles.panelDark}>
            <h2 style={styles.sectionTitleDark}>{platformConfig.commandTitle}</h2>
            <pre style={styles.code}>{sidecarCommand}</pre>
            <button type="button" onClick={copyCommand} style={styles.primaryButton}>
              {copied ? "복사 완료" : platformConfig.copyLabel}
            </button>
            <p style={styles.commandHint}>{platformConfig.commandHint}</p>
          </section>
        </main>

        <PlatformRunbookPanel platform={values.platform} />

        <SidecarRunnerPanel values={values} />

        <SmokeChecklist checks={checks} onRunAll={runAllSmokeChecks} running={runningChecks} />
      </details>
      </div>
    </div>
  );
}
// === ANCHOR: SETUPASSISTANT_SETUPASSISTANT_END ===
// === ANCHOR: SETUPASSISTANT_END ===
