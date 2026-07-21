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

// ── 슬레이트 구역 드래그 ──────────────────────────────────────────────────────
// 화면에 표시된 프레임 위의 드래그를 프레임 대비 비율로 바꾼다. 비율로 저장해야
// 표시 크기·원본 해상도가 달라도 같은 구역을 가리킨다.
export type DragPoint = { x: number; y: number };
export type DisplayBox = { left: number; top: number; width: number; height: number };

const clamp01 = (v: number): number => Math.min(1, Math.max(0, v));
const round4 = (v: number): number => Math.round(v * 10000) / 10000;

export function regionFromDrag(
  from: DragPoint, to: DragPoint, box: DisplayBox,
): { x: number; y: number; w: number; h: number } | null {
  if (box.width <= 0 || box.height <= 0) return null;
  const fx = clamp01((from.x - box.left) / box.width);
  const fy = clamp01((from.y - box.top) / box.height);
  const tx = clamp01((to.x - box.left) / box.width);
  const ty = clamp01((to.y - box.top) / box.height);
  const x = Math.min(fx, tx);
  const y = Math.min(fy, ty);
  const w = Math.abs(tx - fx);
  const h = Math.abs(ty - fy);
  // 클릭이나 미세 흔들림은 구역으로 보지 않는다(실수로 전체가 지워지는 것 방지).
  if (w < 0.01 || h < 0.01) return null;
  return { x: round4(x), y: round4(y), w: round4(w), h: round4(h) };
}

// ── OCR 오독 검출 ────────────────────────────────────────────────────────────
// 씬 모드는 구간이 수백 개라 눈으로 훑기 어렵다. 오독은 대개 구분자 유실로
// 토큰이 붙는 형태라(실기: 040_0080_AC_v01 → 0400080_ACV01) 라벨의 "모양"이
// 다수와 어긋난다. 작품별 포맷을 하드코딩하지 않고, 데이터 자신의 최빈 모양을
// 기준으로 이탈을 찾고 그 템플릿으로 재분해해 교정안을 만든다.

// 토큰을 문자종류(U=대문자, L=소문자, D=숫자, X=기타) 런과 길이로 요약.
export function tokenShape(token: string): string {
  const cls = (c: string): string =>
    /[0-9]/.test(c) ? "D" : /[A-Z]/.test(c) ? "U" : /[a-z]/.test(c) ? "L" : "X";
  let out = "";
  let cur = "";
  let n = 0;
  for (const c of squashWs(token)) {
    const k = cls(c);
    if (k === cur) { n += 1; continue; }
    if (cur) out += `${cur}${n}`;
    cur = k; n = 1;
  }
  if (cur) out += `${cur}${n}`;
  return out;
}

const modeOf = (xs: string[]): string | null => {
  const counts = new Map<string, number>();
  for (const x of xs) counts.set(x, (counts.get(x) ?? 0) + 1);
  let best: string | null = null;
  let bestN = 0;
  for (const [k, n] of counts) if (n > bestN) { best = k; bestN = n; }
  return best;
};

// 라벨 집합의 대표 모양 — 최빈 토큰 개수를 고르고, 그 개수를 가진 라벨들에서
// 위치별 최빈 모양을 뽑는다.
export function labelTemplate(
  labels: string[], delimiters: string[] = DEFAULT_DELIMITERS,
): string[] | null {
  const toks = labels.map((l) => tokenizeSlate(l, delimiters));
  const counts = toks.filter((t) => t.length > 0).map((t) => String(t.length));
  const modal = modeOf(counts);
  if (!modal) return null;
  const n = Number(modal);
  const rows = toks.filter((t) => t.length === n);
  const tpl: string[] = [];
  for (let i = 0; i < n; i += 1) {
    const shape = modeOf(rows.map((r) => tokenShape(r[i] as string)));
    if (!shape) return null;
    tpl.push(shape);
  }
  return tpl;
}

// 모양 문자열("U2D4")을 (문자종류, 길이) 목록으로.
const parseShape = (shape: string): [string, number][] =>
  [...shape.matchAll(/([ULDX])(\d+)/g)].map((m) => [m[1] as string, Number(m[2])]);

const matchesShape = (token: string, shape: string): boolean =>
  tokenShape(token) === shape;

// 구분자를 잃고 붙어버린 라벨을 템플릿 모양대로 다시 쪼갠다. 템플릿을 채우지
// 못하면(문자가 모자라거나 종류가 안 맞으면) null — 억지 교정은 하지 않는다.
function reparse(
  label: string, template: string[], delimiters: string[],
): { label: string; confident: boolean } | null {
  const toks = tokenizeSlate(label, delimiters);
  if (toks.length === template.length
      && toks.every((t, i) => matchesShape(t, template[i] as string))) {
    return null;  // 이미 정상
  }
  const flat = toks.map(squashWs).join("");
  const cls = (c: string): string =>
    /[0-9]/.test(c) ? "D" : /[A-Z]/.test(c) ? "U" : /[a-z]/.test(c) ? "L" : "X";
  let pos = 0;
  const out: string[] = [];
  for (const shape of template) {
    let piece = "";
    for (const [kind, len] of parseShape(shape)) {
      for (let k = 0; k < len; k += 1) {
        const c = flat[pos];
        if (c === undefined || cls(c) !== kind) return null;
        piece += c; pos += 1;
      }
    }
    out.push(piece);
  }
  // 라벨 뒤에는 보통 AC/v01 같은 글자 토큰이 남는다(정상). 하지만 바로 다음 글자가
  // 숫자면 자릿수가 남는다는 뜻 — 어디서 끊어야 할지 모호하니(실기 07510040)
  // 자동 적용 대상에서 뺀다.
  const next = flat[pos];
  return { label: out.join("_"), confident: next === undefined || cls(next) !== "D" };
}

export function suggestLabelFix(
  label: string, template: string[], delimiters: string[] = DEFAULT_DELIMITERS,
): string | null {
  return reparse(label, template, delimiters)?.label ?? null;
}

export type LabelAnomaly = {
  index: number; label: string; suggestion: string | null; confident: boolean;
};

// 템플릿과 어긋나는 라벨만 골라 교정안과 함께 돌려준다. 교정안을 못 만들어도
// (VAL 같은 완전 오독) 이탈 사실은 보고한다 — 사용자가 직접 고칠 수 있게.
export function anomalousLabels(
  labels: string[], delimiters: string[] = DEFAULT_DELIMITERS,
): LabelAnomaly[] {
  const tpl = labelTemplate(labels, delimiters);
  if (!tpl) return [];
  const out: LabelAnomaly[] = [];
  labels.forEach((label, index) => {
    const toks = tokenizeSlate(label, delimiters);
    const ok = toks.length === tpl.length
      && toks.every((t, i) => matchesShape(t, tpl[i] as string));
    if (ok) return;
    const fix = reparse(label, tpl, delimiters);
    out.push({ index, label, suggestion: fix?.label ?? null,
               confident: fix?.confident ?? false });
  });
  return out;
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

// 인접한 동일 라벨 세그먼트를 하나로 합친다(시간축 연장). 씬 한가운데 짧은
// 오독이 씬을 쪼갠 뒤 라벨을 교정하면 같은 라벨이 인접하게 되는데, 이들을
// 병합해야 한 씬이 여러 클립으로 나뉘지 않는다. 비인접(비단조 슬레이트) 동일
// 라벨은 건드리지 않는다.
export function mergeAdjacentSameLabel(segs: SceneSegment[]): SceneSegment[] {
  const out: SceneSegment[] = [];
  for (const s of segs) {
    const last = out[out.length - 1];
    if (last && last.label === s.label) {
      out[out.length - 1] = { ...last, end_ms: s.end_ms };
    } else {
      out.push({ ...s });
    }
  }
  return out;
}

export type LabelFix = { index: number; from: string; to: string };

// 일괄 적용 미리보기용 — 확실한 제안만 before→after로 뽑는다. 애매한 제안(숫자
// 잔여)은 목록에 넣지 않는다(행별 버튼으로만 처리).
export function confidentFixes(
  labels: string[], delimiters: string[] = DEFAULT_DELIMITERS,
): LabelFix[] {
  return anomalousLabels(labels, delimiters)
    .filter((a) => a.confident && a.suggestion && a.suggestion !== a.label)
    .map((a) => ({ index: a.index, from: a.label, to: a.suggestion as string }));
}

// 선택된 교정만 적용한다(시간은 건드리지 않는다). 원본 배열은 변경하지 않는다 —
// 호출자가 적용 전 스냅샷을 그대로 들고 있다가 되돌릴 수 있어야 한다.
// from이 현재 라벨과 다르면 건너뛴다: 씬별에서 만든 목록이 시퀀스별에 적용돼
// 엉뚱한 라벨을 덮어쓰는 사고를 막는다(UI 초기화와 별개의 구조적 안전장치).
export function applyFixes(
  segs: SceneSegment[], fixes: LabelFix[], selected: Set<number>,
): SceneSegment[] {
  let out = segs;
  for (const f of fixes) {
    if (!selected.has(f.index)) continue;
    if (out[f.index]?.label !== f.from) continue;
    out = renameSegment(out, f.index, f.to);
  }
  return out;
}

// 구간의 시간 범위를 썸네일 인덱스 범위로 매핑한다(썸네일 i ≈ t = i*intervalMs).
// 리스트에서 구간을 클릭하면 필름스트립의 이 범위를 하이라이트·중앙정렬한다.
export function segmentThumbRange(
  startMs: number, endMs: number, intervalMs: number, thumbCount: number,
): { from: number; to: number } {
  // 썸네일 i는 시각 i*interval의 단일 프레임이므로 start<=i*iv<end 일 때만 그
  // 구간 소속이다. 시작을 floor로 잡으면 정밀화 후(경계가 2초 배수가 아님) 직전
  // 구간의 썸네일이 딸려 들어와 한 칸이 두 구간에 중복 하이라이트된다.
  const iv = intervalMs > 0 ? intervalMs : 1;
  const from = Math.max(0, Math.min(thumbCount - 1, Math.ceil(startMs / iv)));
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
