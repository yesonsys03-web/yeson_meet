import type { CSSProperties } from "react";
import type { BurnStyle } from "./videoApi";

export function activeSegmentIndex(
  segments: Array<{ start_ms: number; end_ms: number }>,
  currentMs: number,
): number {
  return segments.findIndex(
    (s) => currentMs >= s.start_ms && currentMs < s.end_ms,
  );
}

/** burn(ASS Alignment/MarginV/Fontsize)과 같은 값 체계로 미리보기 오버레이 배치 */
export function overlayStyleFor(style: BurnStyle): CSSProperties {
  const base: CSSProperties = {
    position: "absolute",
    left: "5%",
    right: "5%",
    textAlign: "center",
    fontSize: style.font_size,
    lineHeight: 1.35,
    color: "#fff",
    textShadow: "0 0 4px #000, 0 0 8px #000",
    pointerEvents: "none",
    whiteSpace: "pre-wrap",
  };
  if (style.position === "top") return { ...base, top: style.margin_v };
  return { ...base, bottom: style.margin_v };
}
