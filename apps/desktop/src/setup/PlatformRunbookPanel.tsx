// === ANCHOR: PLATFORM_RUNBOOK_PANEL_START ===
import { PLATFORM_RUNBOOKS } from "./platformRunbook";
import { styles } from "./styles";
import type { SetupPlatform } from "./types";

type PlatformRunbookPanelProps = {
  platform: SetupPlatform;
};

export function PlatformRunbookPanel({ platform }: PlatformRunbookPanelProps) {
  const runbook = PLATFORM_RUNBOOKS[platform];

  return (
    <section style={styles.runbookPanel}>
      <div>
        <p style={styles.eyebrow}>client runbook</p>
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
