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

export function formatMs(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
