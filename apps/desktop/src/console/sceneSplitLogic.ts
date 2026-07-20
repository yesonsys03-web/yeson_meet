// 슬레이트 씬 분할 규칙 UI의 순수 계산. 백엔드 scene_split.tokenize와 동일 규칙
// (미리보기 일치 — 실제 경계 계산은 서버가 단일 출처).
// 기본 구분자에서 공백 제외(`_`, `-`만) — 백엔드 _DEFAULT_DELIMS와 동일. 공백은
// 슬레이트 필드 "안"에 들어가는 경우가 많고(예: "Seq 11B"), OCR이 같은 슬레이트에서
// 공백을 들쭉날쭉 읽으면 토큰 인덱스가 프레임마다 어긋난다. 공백을 필드 구분자로
// 쓰는 슬레이트는 UI의 "공백도 구분자로" 토글로 명시 지정한다.
const DEFAULT_DELIMITERS = ["_", "-"];

export function tokenizeSlate(
  text: string, delimiters: string[] = DEFAULT_DELIMITERS,
): string[] {
  const escaped = delimiters.map((d) => d.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const parts = escaped.length ? text.split(new RegExp(escaped.join("|"))) : [text];
  return parts.map((s) => s.trim()).filter((s) => s.length > 0);
}

// 토큰 내부 공백 제거 — 백엔드 _squash_ws와 동일. OCR이 "Seq01B"/"Seq 01B"로
// 들쭉날쭉 읽어도 같은 라벨이 되게 하고, 파일명에 공백이 안 들어가게 한다.
const squashWs = (t: string): string => t.replace(/\s+/g, "");

export function previewLabel(tokens: string[], uptoIndex: number): string {
  if (uptoIndex < 0 || uptoIndex >= tokens.length) return "";
  return tokens.slice(0, uptoIndex + 1).map(squashWs).join("_");
}

import type { SceneSegment } from "./videoApi";

// 잘못 인식된 구간(예: OCR 노이즈로 생긴 짧은 'VAL')을 이웃 구간에 흡수한다.
// 시간축에 빈틈이 생기지 않도록 이웃이 그 구간의 시간을 넘겨받는다.
// into="prev": 이전 구간이 end_ms를 이 구간 end까지 연장하고 이 구간 제거.
// into="next": 다음 구간이 start_ms를 이 구간 start까지 당기고 이 구간 제거.
export function mergeSegment(
  segs: SceneSegment[], i: number, into: "prev" | "next",
): SceneSegment[] {
  const cur = segs[i];
  if (!cur) return segs;
  const out = segs.slice();
  if (into === "prev" && i > 0) {
    const prev = out[i - 1];
    if (prev) { out[i - 1] = { ...prev, end_ms: cur.end_ms }; out.splice(i, 1); }
  } else if (into === "next" && i < segs.length - 1) {
    const nxt = out[i + 1];
    if (nxt) { out[i + 1] = { ...nxt, start_ms: cur.start_ms }; out.splice(i, 1); }
  }
  return out;
}

// 구간의 시간 범위를 썸네일 인덱스 범위로 매핑한다(썸네일 i ≈ t = i*intervalMs).
// 리스트에서 구간을 클릭하면 필름스트립의 이 범위를 하이라이트·중앙정렬한다.
export function segmentThumbRange(
  startMs: number, endMs: number, intervalMs: number, thumbCount: number,
): { from: number; to: number } {
  const iv = intervalMs > 0 ? intervalMs : 1;
  const from = Math.max(0, Math.min(thumbCount - 1, Math.floor(startMs / iv)));
  const to = Math.max(from, Math.min(thumbCount - 1, Math.ceil(endMs / iv) - 1));
  return { from, to };
}

// 구간 이름(=파일명) 수정.
export function renameSegment(
  segs: SceneSegment[], i: number, label: string,
): SceneSegment[] {
  const cur = segs[i];
  if (!cur) return segs;
  const out = segs.slice();
  out[i] = { ...cur, label };
  return out;
}

export function formatMs(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
