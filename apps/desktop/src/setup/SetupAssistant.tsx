// === ANCHOR: SETUPASSISTANT_START ===
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";
import { loadCredentialsMeta, updateServerWsBase as updateServerWsBaseKeychain } from "./credentials";
import { Field } from "./Field";
import { MeetingQuickStartPanel } from "./MeetingQuickStartPanel";
import { PlatformRunbookPanel } from "./PlatformRunbookPanel";
import { normalizeServerWsBase } from "./serverDiscovery";
import { SmokeChecklist } from "./SmokeChecklist";
import { loadValues, SETUP_VALUES_UPDATED_EVENT, storeValues } from "./setupValues";
import { initialSmokeChecks, runSmokeCheck, SMOKE_CHECK_ORDER } from "./smokeChecks";
import { styles } from "./styles";
import type { SetupValues, SmokeCheckKey, SmokeStatus } from "./types";

// === ANCHOR: SETUPASSISTANT_SETUPASSISTANT_START ===
export function SetupAssistant() {
  const [values, setValues] = useState<SetupValues>(loadValues);
  const [runningChecks, setRunningChecks] = useState(false);
  const [checks, setChecks] = useState(initialSmokeChecks);
  const [subnetBase, setSubnetBase] = useState("");
  const [subnetStatus, setSubnetStatus] = useState<string | null>(null);
  const [subnetScanning, setSubnetScanning] = useState(false);
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

  async function handleSubnetScan() {
    const base = subnetBase.trim();
    setSubnetScanning(true);
    setSubnetStatus("검색 중…");
    try {
      const found = await invoke<string[]>("scan_subnet", { base, port: 8000 });
      if (found.length === 0) {
        setSubnetStatus("이 대역에서 서버를 못 찾았어요");
      } else {
        updateServerWsBase(normalizeServerWsBase(found[0] ?? ""));
        setSubnetStatus(
          found.length > 1
            ? `찾음: ${found.join(", ")} (첫 번째 선택됨)`
            : `찾음: ${found[0] ?? ""}`,
        );
      }
    } catch {
      setSubnetStatus("검색 중 오류가 발생했어요 (Tauri 환경이 아닐 수 있어요)");
    } finally {
      setSubnetScanning(false);
    }
  }

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
        <div style={styles.heroMain}>
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
        <summary style={styles.sectionTitle}>문제 해결 (자동이 안 될 때만 열기)</summary>

        <section style={styles.panel}>
          <h2 style={styles.sectionTitle}>수동 설정 값</h2>
          <p style={styles.runbookIntro}>
            보통은 맨 위 빠른 시작에서 자동으로 채워집니다. 자동 연결이 안 될 때만 직접 확인·수정하세요.
          </p>
          <Field
            label="WebSocket 서버 주소"
            help="서버 IP만 입력해도 됩니다. 예: 192.168.0.51 (자동으로 ws://…:8000 로 변환). 한 PC 테스트는 127.0.0.1."
            value={values.serverWsBase}
            onChange={(value) => updateServerWsBase(value)}
            onBlur={() => updateServerWsBase(normalizeServerWsBase(values.serverWsBase ?? ""))}
          />
          <Field
            label="Device API Key"
            help="서버 콘솔의 Devices에서 발급한 키입니다(키 발급은 서버에서만 합니다). 보통은 빠른 시작 등록 때 자동으로 쓰이며, 보안상 저장하지 않습니다."
            value={values.deviceApiKey}
            secret
            onChange={(value) => updateValue("deviceApiKey", value)}
          />
          <Field
            label="Session ID"
            help="회의를 만들면 자동으로 채워집니다. 자막이 엉뚱한 회의로 갈 때만 확인하세요."
            value={values.sessionId}
            onChange={(value) => updateValue("sessionId", value)}
          />
        </section>

        <section style={{ ...styles.panel, marginTop: 16 }}>
          <h3 style={{ ...styles.sectionTitle, fontSize: 16, marginBottom: 8 }}>서버 자동 검색 (다른 대역)</h3>
          <p style={styles.help}>
            서버가 다른 대역에 있을 때 서버의 앞 3자리 대역을 넣고 검색하세요. 예: 192.168.0
          </p>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <input
              style={{ ...styles.input, flex: 1 }}
              placeholder="192.168.0"
              value={subnetBase}
              onChange={(e) => setSubnetBase(e.currentTarget.value)}
            />
            <button
              style={{
                ...styles.secondaryLightButton,
                width: "auto",
                marginBottom: 0,
                padding: "13px 20px",
                ...(subnetScanning || !subnetBase.trim() ? styles.disabledButton : {}),
              }}
              disabled={subnetScanning || !subnetBase.trim()}
              onClick={handleSubnetScan}
            >
              검색
            </button>
          </div>
          {subnetStatus && <span style={{ ...styles.help, marginTop: 8 }}>{subnetStatus}</span>}
        </section>

        <PlatformRunbookPanel />

        <SmokeChecklist checks={checks} onRunAll={runAllSmokeChecks} running={runningChecks} />
      </details>
      </div>
    </div>
  );
}
// === ANCHOR: SETUPASSISTANT_SETUPASSISTANT_END ===
// === ANCHOR: SETUPASSISTANT_END ===
