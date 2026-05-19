// === ANCHOR: SETUPASSISTANT_START ===
import { useMemo, useState } from "react";
import { Field } from "./Field";
import { PlatformSelector } from "./PlatformSelector";
import { SmokeChecklist } from "./SmokeChecklist";
import { PLATFORM_CONFIG } from "./platformConfig";
import { buildSidecarCommand } from "./sidecarCommands";
import { loadValues, storeValues } from "./setupValues";
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
        audioDeviceName: PLATFORM_CONFIG[platform].audioDeviceName,
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
            담당자가 터미널 명령을 외우지 않아도 되도록, 서버 주소와 회의 정보를 한 곳에 모아 플랫폼별 sidecar 실행 명령을 만듭니다.
          </p>
        </div>
        <div style={styles.statusCard}>
          <span style={styles.statusLabel}>현재 검증 범위</span>
          <strong>서버 접속 + 실행 명령 준비</strong>
          <small>Mac은 BlackHole, Windows는 Voicemeeter 기준으로 실행값을 분리합니다.</small>
        </div>
      </section>

      <main style={styles.grid}>
        <section style={styles.panel}>
          <h2 style={styles.sectionTitle}>1. 관리자에게 받은 값 입력</h2>
          <PlatformSelector value={values.platform} onChange={updatePlatform} />
          <Field
            label="WebSocket 서버 주소"
            help="예: wss://192.168.0.38 — 회의실 PC가 오디오를 보낼 목적지입니다."
            value={values.serverWsBase}
            onChange={(value) => updateValue("serverWsBase", value)}
          />
          <Field
            label="Device API Key"
            help="회의실 PC 출입증입니다. 보안을 위해 브라우저에 저장하지 않고 실행할 때만 사용합니다."
            value={values.deviceApiKey}
            secret
            onChange={(value) => updateValue("deviceApiKey", value)}
          />
          <Field
            label="Session ID"
            help="이번 테스트/회의 번호표입니다."
            value={values.sessionId}
            onChange={(value) => updateValue("sessionId", value)}
          />
          <Field
            label="Viewer URL"
            help="폰이나 노트북에서 자막을 확인할 주소입니다."
            value={values.viewerUrl}
            onChange={(value) => updateValue("viewerUrl", value)}
          />
          <Field
            label="오디오 장치 이름"
            help={platformConfig.audioDeviceHelp}
            value={values.audioDeviceName}
            onChange={(value) => updateValue("audioDeviceName", value)}
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

      <SmokeChecklist checks={checks} onRunAll={runAllSmokeChecks} running={runningChecks} />
    </div>
  );
}
// === ANCHOR: SETUPASSISTANT_SETUPASSISTANT_END ===
// === ANCHOR: SETUPASSISTANT_END ===
