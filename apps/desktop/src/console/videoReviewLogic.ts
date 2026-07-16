import type { CSSProperties } from "react";
import type { BurnStyle, GpuStatus, TranslateEngineInfo } from "./videoApi";

// 엔진 표시 라벨 — 결과보기 재번역 드롭다운과 VideoCaptionPanel 새 작업 드롭다운이
// 같은 규칙을 쓴다(규칙 복제 금지). 리프 모듈에 둬서 두 컴포넌트가 순환 import
// 없이 공유한다.
export function engineLabel(engine: TranslateEngineInfo): string {
  let label = engine.label;
  if (engine.reason) {
    // 이 서버가 런타임 자체를 지원 못하는 티어 — "미설치" 문구는 오히려
    // 오해를 부른다(설치해도 못 쓴다는 뜻이므로 사유를 그대로 보여준다).
    label += ` (${engine.reason})`;
  } else if (!engine.available) {
    label += engine.value === "gemini" ? " (서버에 키 없음)" : " (서버에 미설치)";
  }
  return label;
}

// GPU가 켜져 있고 팩도 설치됐는데 CUDA 인식에 실패한 경우에만 경고를 보여준다 —
// 꺼짐/미설치 상태는 이미 다른 UI(버튼/설치 안내)가 설명하므로 중복 표시하지 않는다.
export function shouldShowCudaWarning(
  gpu: Pick<GpuStatus, "enabled" | "installed" | "cuda_ok">,
): boolean {
  // cuda_ok가 undefined(구버전 서버 응답 등)면 경고를 띄우지 않는다 — CUDA가 실제로
  // 실패했다는 신호(cuda_ok === false)일 때만 표시해 스퓨리어스 경고를 막는다.
  return gpu.enabled && gpu.installed && gpu.cuda_ok === false;
}

// job.title은 자유 입력(YouTube 제목 등)이라 "Rig/Puppet" 처럼 경로 구분자를
// 포함할 수 있다. 저장 다이얼로그 defaultPath/브라우저 다운로드 파일명 양쪽에
// 안전하게 쓰기 위해 OS 금지문자를 "-"로 치환하고, Windows가 허용하지 않는
// 파일명 끝의 점/공백도 제거한다 (빈 결과는 "video" 폴백).
export function sanitizeFilename(name: string): string {
  const cleaned = name.replace(/[/\\:*?"<>|]/g, "-").replace(/[. ]+$/, "").trim();
  return cleaned || "video";
}

export function activeSegmentIndex(
  segments: Array<{ start_ms: number; end_ms: number }>,
  currentMs: number,
): number {
  return segments.findIndex(
    (s) => currentMs >= s.start_ms && currentMs < s.end_ms,
  );
}

/** 번역기가 원문을 그대로 복사한 줄인가 — 서버 is_source_copy와 같은 규칙.
 *
 * 서버의 재번역 대상 선정과 **같은 규칙이어야 한다**: 배지에 표시한 수와 버튼이
 * 실제로 고치는 수가 어긋나면 안 된다. 서버의 english_leak(ascii 비율) 쪽은
 * 사후 확인 전용이므로 여기 재현하지 않는다 — 그래서 클라에 임계 상수가 없고,
 * 드리프트할 것도 없다.
 */
export function isSourceCopy(textEn: string, textKo: string): boolean {
  const ko = textKo.trim();
  if (!ko) return false;   // 사용자가 의도적으로 비운 줄
  return ko === textEn.trim();
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
