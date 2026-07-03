// === ANCHOR: TUNNEL_DEGRADED_BANNER_START ===
// P4.2 degraded + LAN fallback banner for the server console. Shown when the
// public cloudflared tunnel drops on its own (Rust `tunnel_status.degraded`).
// LAN viewing is unaffected — this alerts the operator that the PUBLIC link is
// dead and offers recovery (re-publish) or a clean LAN fallback. Recovery
// actions reuse the console's existing tunnel handlers (single source of truth
// for invoke/busy/error state); the banner stays presentational plus a one-shot
// LAN-base lookup. Modeled on the client app's `NativeCaptureBanner`.
import type { CSSProperties } from "react";
import { useEffect, useState } from "react";

import { invoke } from "@tauri-apps/api/core";

type Props = {
  degraded: boolean;
  /** The now-dead public URL (from `tunnel_status.url` while degraded). */
  deadUrl: string | null;
  serverPort: number;
  running: boolean;
  meetingLive: boolean;
  busy: boolean;
  /** Re-publish a fresh tunnel (= console `onGoLive`). */
  onRepublish: () => void;
  /** Clear the public base + return to LAN-only (= console `onStopPublic`). */
  onFallbackLan: () => void;
};

export default function TunnelDegradedBanner({
  degraded,
  deadUrl,
  serverPort,
  running,
  meetingLive,
  busy,
  onRepublish,
  onFallbackLan,
}: Props) {
  const [dismissedUrl, setDismissedUrl] = useState<string | null>(null);
  const [lanBase, setLanBase] = useState<string | null>(null);

  // Look up the LAN fallback base once whenever a degradation surfaces. The IP
  // is best-effort; on failure we fall back to a generic hint below.
  useEffect(() => {
    if (!degraded) return;
    let alive = true;
    invoke<string | null>("lan_viewer_base_cmd", { serverPort })
      .then((base) => {
        if (alive) setLanBase(base ?? null);
      })
      .catch(() => {
        if (alive) setLanBase(null);
      });
    return () => {
      alive = false;
    };
  }, [degraded, serverPort]);

  if (!degraded) return null;
  // Dismiss is sticky per dead URL: a fresh drop (new URL) re-raises the banner.
  if (deadUrl != null && deadUrl === dismissedUrl) return null;

  return (
    <div role="alert" style={bannerStyle}>
      <div style={textCol}>
        <span style={{ fontWeight: 700 }}>공개 터널이 끊겼습니다.</span>
        <span>
          공개 링크(QR)로 접속한 외부 시청자는 더 이상 연결되지 않습니다. 같은 Wi-Fi(LAN)에 있는 시청자는
          영향받지 않습니다.
        </span>
        {deadUrl ? <span style={mono}>끊긴 주소: {deadUrl}</span> : null}
        {lanBase ? (
          <span style={mono}>
            LAN 뷰어 주소: {lanBase}/v/&lt;회의 토큰&gt;
          </span>
        ) : (
          <span style={{ fontSize: 12, opacity: 0.85 }}>
            LAN 시청자는 서버 컴퓨터 주소(http://&lt;서버 IP&gt;:{serverPort})로 계속 접속할 수 있습니다.
          </span>
        )}
      </div>
      <div style={actionsCol}>
        <button
          type="button"
          style={primaryAction}
          onClick={onRepublish}
          disabled={busy || !running}
          title={
            !running
              ? "서버를 먼저 시작하세요"
              : meetingLive
                ? "새 공개 터널을 다시 엽니다 — 새 주소가 발급되므로 시청자에게 링크(QR)를 다시 공유하세요"
                : "새 공개 터널을 다시 엽니다"
          }
        >
          다시 공개
        </button>
        <button type="button" style={mutedAction} onClick={onFallbackLan} disabled={busy}>
          LAN 전용으로
        </button>
        <button type="button" style={mutedAction} onClick={() => setDismissedUrl(deadUrl)}>
          닫기
        </button>
      </div>
    </div>
  );
}

const bannerStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: 12,
  padding: "10px 14px",
  margin: "12px 0 0",
  borderRadius: "var(--ys-radius-md)",
  border: "1px solid var(--ys-warning-border)",
  background: "var(--ys-warning-bg)",
  color: "var(--ys-warning-text)",
  fontSize: 13,
};

const textCol: CSSProperties = { display: "flex", flexDirection: "column", gap: 4 };
const actionsCol: CSSProperties = { display: "flex", gap: 8, flexShrink: 0, alignItems: "center" };
const mono: CSSProperties = {
  fontFamily: "var(--ys-font-mono)",
  fontSize: 12,
  wordBreak: "break-all",
};
const baseAction: CSSProperties = {
  padding: "6px 12px",
  borderRadius: "var(--ys-radius-control)",
  border: "1px solid var(--ys-border-strong)",
  cursor: "pointer",
  fontSize: 12,
};
const primaryAction: CSSProperties = {
  ...baseAction,
  background: "var(--ys-accent-strong)",
  borderColor: "var(--ys-accent-strong)",
  color: "var(--ys-on-accent)",
  fontWeight: 700,
};
const mutedAction: CSSProperties = { ...baseAction, background: "transparent", color: "var(--ys-text-label)" };
// === ANCHOR: TUNNEL_DEGRADED_BANNER_END ===
