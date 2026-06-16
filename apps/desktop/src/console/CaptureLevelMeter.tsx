// === ANCHOR: CAPTURE_LEVEL_METER_START ===
import type { CSSProperties } from "react";

import { SEGMENTS, dbfsToSegments, segmentColorRole, type SegmentRole } from "./captureLevel";
import type { CaptureState } from "./captureStatus";

const GREEN = "#22c55e";
const YELLOW = "#fde047";
const RED = "#f87171";
const EMPTY_BG = "#1e293b";
const EMPTY_BORDER = "#334155";

const ROLE_COLOR: Record<SegmentRole, string> = { green: GREEN, yellow: YELLOW, red: RED };

/**
 * Live loudness meter shown beside the capture-status chip.
 * Renders only in active/silent/no_audio states (the chip carries
 * connecting/transport_down); silent and no_audio always show an empty bar,
 * which also covers Windows silence (no chunks → dbfs null).
 */
export function CaptureLevelMeter({ dbfs, state }: { dbfs: number | null; state: CaptureState }) {
  if (state === "connecting" || state === "transport_down") return null;
  const filled = state === "silent" || state === "no_audio" ? 0 : dbfsToSegments(dbfs ?? -120);

  const wrap: CSSProperties = { display: "inline-flex", alignItems: "center", gap: 2 };
  return (
    <span role="img" aria-label={`캡처 레벨 ${filled}/${SEGMENTS}`} title="실시간 캡처 음량" style={wrap}>
      {Array.from({ length: SEGMENTS }, (_, i) => {
        const on = i < filled;
        const cell: CSSProperties = {
          width: 4,
          height: 11,
          borderRadius: 1,
          boxSizing: "border-box",
          background: on ? ROLE_COLOR[segmentColorRole(i)] : EMPTY_BG,
          border: on ? "none" : `1px solid ${EMPTY_BORDER}`,
        };
        return <span key={i} style={cell} />;
      })}
    </span>
  );
}
// === ANCHOR: CAPTURE_LEVEL_METER_END ===
