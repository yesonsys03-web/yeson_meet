// === ANCHOR: CAPTURE_LEVEL_METER_START ===
import type { CSSProperties } from "react";

import { CLIP_DBFS, SEGMENTS, WARN_DBFS, dbfsToSegments, segmentEdgeDbfs } from "./captureLevel";
import type { CaptureState } from "./captureStatus";

const GREEN = "#22c55e";
const YELLOW = "#fde047";
const RED = "#f87171";
const EMPTY_BG = "#1e293b";
const EMPTY_BORDER = "#334155";

function litColor(index: number): string {
  const edge = segmentEdgeDbfs(index);
  if (edge > CLIP_DBFS) return RED;
  if (edge > WARN_DBFS) return YELLOW;
  return GREEN;
}

/**
 * Live loudness meter shown beside the capture-status chip.
 * Renders only in active/silent states (the chip carries connecting/transport_down);
 * silent always shows an empty bar, which also covers Windows silence (no chunks → dbfs null).
 */
export function CaptureLevelMeter({ dbfs, state }: { dbfs: number | null; state: CaptureState }) {
  if (state === "connecting" || state === "transport_down") return null;
  const filled = state === "silent" ? 0 : dbfsToSegments(dbfs ?? -120);

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
          background: on ? litColor(i) : EMPTY_BG,
          border: on ? "none" : `1px solid ${EMPTY_BORDER}`,
        };
        return <span key={i} style={cell} />;
      })}
    </span>
  );
}
// === ANCHOR: CAPTURE_LEVEL_METER_END ===
