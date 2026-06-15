// === ANCHOR: SETUPASSISTANT_START ===
import { useEffect, useMemo, useState } from "react";
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
          <p style={styles.eyebrow}>yeson-meet · Mac / Windows client setup</p>
          <h1 style={styles.title}>회의실 PC 실행 준비</h1>
          <p style={styles.subtitle}>
            담당자가 탭을 오가거나 터미널 명령을 외우지 않아도 되도록, 로그인·회의 생성·sidecar 실행·자막 확인을 한 화면에 모았습니다.
          </p>
        </div>
        <div style={styles.statusCard}>
          <span style={styles.statusLabel}>현재 검증 범위</span>
          <strong>회의 생성 + sidecar 실행</strong>
          <small>Session ID와 Viewer URL은 회의를 만들면 자동으로 실행값에 반영됩니다.</small>
        </div>
      </section>

      <MeetingQuickStartPanel />

      <main style={styles.grid}>
        <section style={styles.panel}>
          <h2 style={styles.sectionTitle}>실행 환경 값</h2>
          <PlatformSelector value={values.platform} onChange={updatePlatform} />
          <Field
            label="WebSocket 서버 주소"
            help="로컬 테스트는 ws://127.0.0.1:8000, LAN HTTPS 테스트는 wss://192.168.0.38 처럼 입력합니다."
            value={values.serverWsBase}
            onChange={(value) => updateValue("serverWsBase", value)}
          />
          <Field
            label="테스트용 오디오 키 (Device API Key)"
            help="sidecar가 서버에 접속할 때 필요한 회의실 PC용 키입니다. 보안을 위해 저장하지 않으므로 Sidecar 시작 직전에 붙여넣어야 합니다."
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
            help="dev/fallback sounddevice 실행 전용입니다. 비워두면 패키지 앱은 번들된 네이티브 sidecar를 사용합니다."
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
    </div>
  );
}
// === ANCHOR: SETUPASSISTANT_SETUPASSISTANT_END ===
// === ANCHOR: SETUPASSISTANT_END ===
