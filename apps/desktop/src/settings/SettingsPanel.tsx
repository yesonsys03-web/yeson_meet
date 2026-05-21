// === ANCHOR: SETTINGS_PANEL_START ===
import { useState } from "react";
import { LogSettingsPanel } from "./LogSettingsPanel";
import { settingsStyles } from "./settingsStyles";

type SettingsSectionId = "log";

type SettingsSection = {
  id: SettingsSectionId;
  label: string;
  summary: string;
};

const settingsSections: SettingsSection[] = [
  {
    id: "log",
    label: "Log",
    summary: "API, WebSocket, sidecar 흐름을 실시간으로 모아 latency를 추적합니다.",
  },
];

export function SettingsPanel() {
  const [activeSection, setActiveSection] = useState<SettingsSectionId>("log");

  return (
    <div style={settingsStyles.page}>
      <section style={settingsStyles.hero}>
        <div>
          <p style={settingsStyles.eyebrow}>settings · diagnostics</p>
          <h1 style={settingsStyles.title}>운영 로그</h1>
          <p style={settingsStyles.subtitle}>
            지연을 잡으려면 “어디에서 느려졌는지”가 먼저 보여야 합니다. Settings는 앞으로 진단 도구가 늘어나도 섹션만 추가하면 되도록 구성했습니다.
          </p>
        </div>
        <div style={settingsStyles.heroCard}>
          <strong>Latency trace ready</strong>
          <span>HTTP · WebSocket · sidecar stdout/stderr · console</span>
        </div>
      </section>

      <div style={settingsStyles.shell}>
        <aside style={settingsStyles.sectionNav} aria-label="Settings sections">
          {settingsSections.map((section) => (
            <button
              key={section.id}
              type="button"
              onClick={() => setActiveSection(section.id)}
              style={{
                ...settingsStyles.sectionButton,
                ...(activeSection === section.id ? settingsStyles.sectionButtonActive : null),
              }}
            >
              <strong>{section.label}</strong>
              <span>{section.summary}</span>
            </button>
          ))}
        </aside>
        <main style={settingsStyles.sectionContent}>{activeSection === "log" ? <LogSettingsPanel /> : null}</main>
      </div>
    </div>
  );
}
// === ANCHOR: SETTINGS_PANEL_END ===
