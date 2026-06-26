// === ANCHOR: PLATFORM_RUNBOOK_PANEL_START ===
import { useState } from "react";
import { defaultPlatform } from "./platformConfig";
import { PLATFORM_RUNBOOKS } from "./platformRunbook";
import { styles } from "./styles";
import type { SetupPlatform } from "./types";

const RUNBOOK_TABS: { key: SetupPlatform; label: string }[] = [
  { key: "mac", label: "Mac" },
  { key: "windows", label: "Windows" },
];

export function PlatformRunbookPanel() {
  const [platform, setPlatform] = useState<SetupPlatform>(defaultPlatform);
  const runbook = PLATFORM_RUNBOOKS[platform];

  return (
    <section style={styles.runbookPanel}>
      <div>
        <p style={styles.eyebrow}>client runbook</p>
        <div style={styles.runbookTabs}>
          {RUNBOOK_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setPlatform(tab.key)}
              style={platform === tab.key ? styles.runbookTabActive : styles.runbookTab}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <h2 style={styles.sectionTitle}>{runbook.title}</h2>
        <p style={styles.runbookIntro}>{runbook.intro}</p>
      </div>
      <div style={styles.runbookGrid}>
        {runbook.steps.map((step, index) => (
          <article key={step.title} style={styles.runbookCard}>
            <span style={styles.runbookNumber}>{index + 1}</span>
            <strong>{step.title}</strong>
            <p>{step.detail}</p>
          </article>
        ))}
      </div>
      <p style={styles.runbookReminder}>{runbook.reminder}</p>
    </section>
  );
}
// === ANCHOR: PLATFORM_RUNBOOK_PANEL_END ===
