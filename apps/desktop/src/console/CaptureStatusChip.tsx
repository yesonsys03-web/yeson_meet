// === ANCHOR: CAPTURE_STATUS_CHIP_START ===
import type { CSSProperties } from "react";

import type { CaptureState } from "./captureStatus";

const PRESENTATION: Record<CaptureState, { label: string; color: string; bg: string; border: string }> = {
  connecting: { label: "연결 중", color: "#cbd5e1", bg: "#1e293b", border: "#475569" },
  active: { label: "정상", color: "#86efac", bg: "#0f2a1a", border: "#15803d" },
  silent: { label: "무음", color: "#fde047", bg: "#2a2408", border: "#a16207" },
  transport_down: { label: "전송 끊김", color: "#fca5a5", bg: "#2a0f0f", border: "#b91c1c" },
};

const TITLE: Record<CaptureState, string> = {
  connecting: "오디오 캡처 연결 중",
  active: "오디오가 정상 캡처·전송되고 있습니다",
  silent: "10초 이상 오디오가 없습니다 (캡처는 정상) — 예상 밖이면 소스/장치를 확인하세요",
  transport_down: "서버로의 오디오 전송이 끊겼습니다 (재연결 시도 중)",
};

export function CaptureStatusChip({ state }: { state: CaptureState }) {
  const p = PRESENTATION[state];
  const chipStyle: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "2px 8px",
    borderRadius: 999,
    border: `1px solid ${p.border}`,
    background: p.bg,
    color: p.color,
    fontSize: 12,
    fontWeight: 600,
    whiteSpace: "nowrap",
  };
  const dotStyle: CSSProperties = {
    width: 7,
    height: 7,
    borderRadius: "50%",
    background: p.color,
    flexShrink: 0,
  };
  return (
    <span role="status" title={TITLE[state]} style={chipStyle}>
      <span style={dotStyle} />
      {p.label}
    </span>
  );
}
// === ANCHOR: CAPTURE_STATUS_CHIP_END ===
