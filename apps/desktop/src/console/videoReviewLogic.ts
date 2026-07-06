import type { CSSProperties } from "react";
import type { BurnStyle } from "./videoApi";

// job.title은 자유 입력(YouTube 제목 등)이라 "Rig/Puppet" 처럼 경로 구분자를
// 포함할 수 있다. 저장 다이얼로그 defaultPath/브라우저 다운로드 파일명 양쪽에
// 안전하게 쓰기 위해 OS 금지문자를 "-"로 치환한다.
export function sanitizeFilename(name: string): string {
  return name.replace(/[/\\:*?"<>|]/g, "-");
}

export function activeSegmentIndex(
  segments: Array<{ start_ms: number; end_ms: number }>,
  currentMs: number,
): number {
  return segments.findIndex(
    (s) => currentMs >= s.start_ms && currentMs < s.end_ms,
  );
}

/**
 * burn(ffmpeg subtitles 필터가 SRT→ASS 변환 시 사용하는 libass PlayResY=288
 * 캔버스)과 같은 좌표계로 미리보기 오버레이를 배치한다. ASS의
 * Fontsize/MarginV는 PlayResY=288 기준값이며 실제 렌더 높이로 스케일되므로,
 * 미리보기도 `renderedVideoHeight / 288` 배로 스케일해야 burn 결과와 일치한다.
 * renderedVideoHeight 미전달 시 scale=1 (기존 동작 유지).
 */
export function overlayStyleFor(
  style: BurnStyle,
  renderedVideoHeight?: number,
): CSSProperties {
  const scale = renderedVideoHeight && renderedVideoHeight > 0
    ? renderedVideoHeight / 288
    : 1;
  const base: CSSProperties = {
    position: "absolute",
    left: "5%",
    right: "5%",
    textAlign: "center",
    fontSize: style.font_size * scale,
    lineHeight: 1.35,
    color: style.color,
    textShadow: "0 0 4px #000, 0 0 8px #000",
    pointerEvents: "none",
    whiteSpace: "pre-wrap",
  };
  if (style.position === "top") return { ...base, top: style.margin_v * scale };
  return { ...base, bottom: style.margin_v * scale };
}
