// === ANCHOR: LOG_SETTINGS_PANEL_START ===
import { useEffect, useMemo, useRef, useState } from "react";
import {
  appLogger,
  clearAppLogs,
  formatAppLogEntry,
  saveAppLogSnapshot,
  subscribeAppLogs,
  type AppLogEntry,
  type AppLogLevel,
} from "../diagnostics/appLog";
import { settingsStyles } from "./settingsStyles";

type LevelFilter = "all" | AppLogLevel;

const levelFilters: LevelFilter[] = ["all", "debug", "info", "warn", "error"];

export function LogSettingsPanel() {
  const [entries, setEntries] = useState<AppLogEntry[]>([]);
  const [levelFilter, setLevelFilter] = useState<LevelFilter>("all");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [saveStatus, setSaveStatus] = useState("아직 저장하지 않았습니다.");
  const [saving, setSaving] = useState(false);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => subscribeAppLogs(setEntries), []);

  const sources = useMemo(() => ["all", ...Array.from(new Set(entries.map((entry) => entry.source))).sort()], [entries]);
  const visibleEntries = useMemo(
    () => entries.filter((entry) => (levelFilter === "all" || entry.level === levelFilter) && (sourceFilter === "all" || entry.source === sourceFilter)),
    [entries, levelFilter, sourceFilter],
  );
  const latestLatency = useMemo(() => latestLatencyLabel(entries), [entries]);
  const errorCount = useMemo(() => entries.filter((entry) => entry.level === "error").length, [entries]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ block: "end" });
  }, [visibleEntries.length]);

  async function saveLogs() {
    setSaving(true);
    try {
      const path = await saveAppLogSnapshot(entries);
      setSaveStatus(`저장 완료: ${path}`);
      appLogger.info("settings", "App logs saved", { detail: `${entries.length} entries` });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setSaveStatus(`저장 실패: ${detail}`);
      appLogger.error("settings", "App log save failed", { detail });
    } finally {
      setSaving(false);
    }
  }

  function addMarker() {
    appLogger.info("operator", "Manual latency marker added");
  }

  function clearLogs() {
    clearAppLogs();
    appLogger.info("settings", "App logs cleared");
  }

  return (
    <section style={settingsStyles.logPanel}>
      <div style={settingsStyles.logHeader}>
        <div>
          <p style={settingsStyles.eyebrow}>live app log</p>
          <h2 style={settingsStyles.sectionTitle}>실시간 로그</h2>
          <p style={settingsStyles.helperText}>회의 시작부터 자막 수신까지의 경계를 시간과 함께 남깁니다. 저장한 파일은 latency 재현 공유용으로 바로 사용할 수 있습니다.</p>
        </div>
        <div style={settingsStyles.metricGrid}>
          <Metric label="entries" value={String(entries.length)} />
          <Metric label="errors" value={String(errorCount)} tone={errorCount > 0 ? "danger" : "normal"} />
          <Metric label="latest latency" value={latestLatency} />
        </div>
      </div>

      <div style={settingsStyles.toolbar}>
        <label style={settingsStyles.selectLabel}>
          Level
          <select value={levelFilter} onChange={(event) => setLevelFilter(event.target.value as LevelFilter)} style={settingsStyles.select}>
            {levelFilters.map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </label>
        <label style={settingsStyles.selectLabel}>
          Source
          <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)} style={settingsStyles.select}>
            {sources.map((source) => (
              <option key={source} value={source}>
                {source}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={addMarker} style={settingsStyles.secondaryButton}>
          마커 추가
        </button>
        <button type="button" onClick={clearLogs} style={settingsStyles.secondaryButton}>
          비우기
        </button>
        <button type="button" onClick={saveLogs} disabled={saving || entries.length === 0} style={{ ...settingsStyles.primaryButton, ...(saving || entries.length === 0 ? settingsStyles.disabledButton : null) }}>
          {saving ? "저장 중..." : "로그 저장"}
        </button>
      </div>

      <p style={settingsStyles.saveStatus}>{saveStatus}</p>

      <div style={settingsStyles.logViewport} aria-live="polite">
        {visibleEntries.length === 0 ? (
          <p style={settingsStyles.emptyLog}>표시할 로그가 없습니다. Setup 또는 Live Meeting 흐름을 실행하면 여기에 실시간으로 쌓입니다.</p>
        ) : (
          visibleEntries.map((entry) => <LogLine key={entry.id} entry={entry} />)
        )}
        <div ref={logEndRef} />
      </div>
    </section>
  );
}

function Metric({ label, value, tone = "normal" }: { label: string; value: string; tone?: "normal" | "danger" }) {
  return (
    <div style={{ ...settingsStyles.metricCard, ...(tone === "danger" ? settingsStyles.metricDanger : null) }}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function LogLine({ entry }: { entry: AppLogEntry }) {
  return (
    <div style={{ ...settingsStyles.logLine, ...levelStyle(entry.level) }}>
      <span style={settingsStyles.logTimestamp}>{timeLabel(entry.ts)}</span>
      <span style={settingsStyles.logLevel}>{entry.level}</span>
      <span style={settingsStyles.logSource}>{entry.source}</span>
      <code style={settingsStyles.logMessage}>{formatAppLogEntry(entry)}</code>
    </div>
  );
}

function levelStyle(level: AppLogLevel) {
  if (level === "error") return settingsStyles.logLineError;
  if (level === "warn") return settingsStyles.logLineWarn;
  if (level === "debug") return settingsStyles.logLineDebug;
  return settingsStyles.logLineInfo;
}

function latestLatencyLabel(entries: AppLogEntry[]): string {
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const entry = entries[index];
    if (entry && typeof entry.durationMs === "number") return `${Math.round(entry.durationMs)}ms`;
  }
  return "n/a";
}

function timeLabel(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString();
}
// === ANCHOR: LOG_SETTINGS_PANEL_END ===
