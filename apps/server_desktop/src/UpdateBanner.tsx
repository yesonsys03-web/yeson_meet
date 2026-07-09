// === ANCHOR: UPDATE_BANNER_START ===
import type { CSSProperties } from "react";

import type { UpdateStatus } from "./updater/autoUpdate";

type UpdateBannerProps = {
  status: UpdateStatus;
  onCheckNow: () => void;
  onApplyNow: () => void;
};

export function UpdateBanner({ status, onCheckNow, onApplyNow }: UpdateBannerProps) {
  const checking = status.kind === "checking" || status.kind === "downloading";
  return (
    <div style={styles.box}>
      {status.kind === "ready" ? (
        <>
          <p style={styles.ready}>v{status.version} 준비됨 — 재시작하여 적용</p>
          <button type="button" style={styles.apply} onClick={onApplyNow}>
            재시작하여 업데이트
          </button>
        </>
      ) : status.kind === "downloading" ? (
        <p style={styles.hint}>
          업데이트 내려받는 중{status.percent != null ? ` (${status.percent}%)` : "…"}
        </p>
      ) : status.kind === "error" ? (
        <p style={styles.hint}>업데이트 버전이 없습니다.</p>
      ) : status.kind === "up-to-date" ? (
        <p style={styles.hint}>현재 최신 버전입니다.</p>
      ) : null}
      <button type="button" style={styles.check} onClick={onCheckNow} disabled={checking}>
        {status.kind === "checking" ? "확인 중…" : "지금 업데이트 확인"}
      </button>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  box: { display: "grid", gap: 8 },
  ready: { margin: 0, fontSize: 12, fontWeight: 800, color: "var(--ys-success-text)", lineHeight: 1.4 },
  hint: { margin: 0, fontSize: 11, color: "var(--ys-text-faint)", lineHeight: 1.4 },
  apply: {
    padding: "8px 10px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-accent-strong)",
    background: "var(--ys-accent-strong)",
    color: "var(--ys-on-accent)",
    fontSize: 12,
    fontWeight: 900,
    cursor: "pointer",
  },
  check: {
    padding: "6px 10px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-border-subtle)",
    background: "transparent",
    color: "var(--ys-text-label)",
    fontSize: 11,
    fontWeight: 700,
    cursor: "pointer",
  },
};
// === ANCHOR: UPDATE_BANNER_END ===
