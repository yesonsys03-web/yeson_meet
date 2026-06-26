// === ANCHOR: SMOKECHECKLIST_START ===
import { SMOKE_CHECK_ORDER } from "./smokeChecks";
import { statusStyles, styles } from "./styles";
import type { SmokeCheck, SmokeCheckKey } from "./types";

type SmokeChecklistProps = {
  checks: Record<SmokeCheckKey, SmokeCheck>;
  onRunAll: () => void;
  running: boolean;
};

const statusLabel = {
  idle: "대기",
  checking: "확인 중",
  ok: "통과",
  fail: "확인 필요",
};

// === ANCHOR: SMOKECHECKLIST_SMOKECHECKLIST_START ===
export function SmokeChecklist({ checks, onRunAll, running }: SmokeChecklistProps) {
  return (
    <section style={styles.checklist}>
      <div style={styles.checklistHeader}>
        <div>
          <h2 style={styles.sectionTitle}>회의 시작 전 빠른 점검</h2>
          <p style={styles.checklistIntro}>Mac·Windows 공통으로, 서버와 Gemini가 준비됐는지 회의 전에 확인합니다.</p>
        </div>
        <button type="button" onClick={onRunAll} style={styles.smallButton} disabled={running}>
          {running ? "확인 중..." : `${SMOKE_CHECK_ORDER.length}개 항목 확인`}
        </button>
      </div>
      <div style={styles.smokeList}>
        {SMOKE_CHECK_ORDER.map((key) => {
          const check = checks[key];
          return (
            <article key={check.key} style={styles.smokeItem}>
              <div>
                <strong>{check.label}</strong>
                <p style={styles.smokeDescription}>{check.description}</p>
                <p style={styles.smokeDetail}>{check.detail}</p>
              </div>
              <span style={{ ...styles.smokeBadge, ...statusStyles[check.status] }}>
                {statusLabel[check.status]}
              </span>
            </article>
          );
        })}
      </div>
    </section>
  );
}
// === ANCHOR: SMOKECHECKLIST_SMOKECHECKLIST_END ===
// === ANCHOR: SMOKECHECKLIST_END ===
