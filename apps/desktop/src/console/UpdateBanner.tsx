// === ANCHOR: UPDATE_BANNER_START ===
import { consoleStyles } from "./consoleStyles";
import { isMacOS, type UpdateStatus } from "../updater/autoUpdate";

type UpdateBannerProps = {
  status: UpdateStatus;
  onCheckNow: () => void;
  onApplyNow: () => void;
};

// macOS re-checks the screen-recording (TCC) grant after the binary's cdhash
// changes (unsigned app). Warn the operator so a post-update prompt isn't a
// surprise — same root cause as the existing "ghost permission" banner.
const MAC_PERMISSION_NOTE = "Mac은 업데이트 후 화면기록 권한 재확인이 필요할 수 있습니다.";

export function UpdateBanner({ status, onCheckNow, onApplyNow }: UpdateBannerProps) {
  const onMac = typeof navigator !== "undefined" && isMacOS(navigator.platform);
  const checking = status.kind === "checking" || status.kind === "downloading";
  return (
    <div style={consoleStyles.updateBox}>
      {status.kind === "ready" ? (
        <>
          <p style={consoleStyles.updateReady}>v{status.version} 준비됨 — 재시작하여 적용</p>
          <button type="button" style={consoleStyles.updateApply} onClick={onApplyNow}>
            재시작하여 업데이트
          </button>
          {onMac ? <p style={consoleStyles.updateNote}>{MAC_PERMISSION_NOTE}</p> : null}
        </>
      ) : status.kind === "downloading" ? (
        <p style={consoleStyles.updateHint}>
          업데이트 내려받는 중{status.percent != null ? ` (${status.percent}%)` : "…"}
        </p>
      ) : status.kind === "error" ? (
        <p style={consoleStyles.updateHint}>업데이트 확인 실패 — 다음에 다시 시도합니다.</p>
      ) : null}
      <button type="button" style={consoleStyles.updateCheck} onClick={onCheckNow} disabled={checking}>
        {status.kind === "checking" ? "확인 중…" : "지금 업데이트 확인"}
      </button>
    </div>
  );
}
// === ANCHOR: UPDATE_BANNER_END ===
