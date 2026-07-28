// 슬레이트 씬 분할 규칙 UI의 순수 계산. 백엔드 scene_split.tokenize와 동일 규칙
// (미리보기 일치 — 실제 경계 계산은 서버가 단일 출처).
// 기본 구분자에서 공백 제외(`_`, `-`만) — 백엔드 _DEFAULT_DELIMS와 동일. 공백은
// 슬레이트 필드 "안"에 들어가는 경우가 많고(예: "Seq 11B"), OCR이 같은 슬레이트에서
// 공백을 들쭉날쭉 읽으면 토큰 인덱스가 프레임마다 어긋난다. 공백을 필드 구분자로
// 쓰는 슬레이트는 UI의 "공백도 구분자로" 토글로 명시 지정한다.
// "/"는 OCR이 "_"를 어긋 읽는 상수적 오독 — 구분자로 두면 키가 정렬된다(백엔드 동일).
const DEFAULT_DELIMITERS = ["_", "-", "/"];

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
// 라벨을 "<접두><번호>"로 되살린다 — 이 쇼의 모든 슬레이트가 같은 접두로
// 시작한다는 사실(modalLabelPrefix)을 근거로 삼는다. 두 종류의 오독을 받는다:
//   ① 접두가 통째로/일부 잘림: '678'·'ene8' → 앞 글자가 접두의 꼬리와 겹친다
//   ② 접두 글자가 깨짐: 'Scéne639'·'Sgene663'·'Scenel625' → 접두와 한두 글자 차이
// 둘 다 아닌 머리('BOBBYp'·'cane')는 접두 복원이 아니라 딴 텍스트다 — 손대지 않는다.
// maxDigits는 코퍼스에서 관측된 번호 자릿수 상한 — '20206'을 'Scene20206'으로
// 만드는 헛제안을 막는다(실기).
function repairWithPrefix(
  prefix: string, label: string, maxDigits: number,
): string | null {
  const m = /^([^0-9]*)([0-9]+)$/.exec(label);
  if (!m) return null;
  const [, head = "", digits = ""] = m;
  if (head === prefix || digits.length > maxDigits) return null;
  const isTail = prefix.endsWith(head);   // ① 잘린 접두(빈 머리 포함)
  const close = editDistance(head.toLowerCase(), prefix.toLowerCase()) <= 2;
  if (!isTail && !close) return null;
  return prefix + digits;
}

export function anomalousLabels(
  labels: string[], delimiters: string[] = DEFAULT_DELIMITERS,
): LabelAnomaly[] {
  const tpl = labelTemplate(labels, delimiters);
  if (!tpl) return [];
  // 이 쇼의 정상 라벨 모양과 공통 접두 — 템플릿(자릿수까지 고정)이 못 고치는
  // '접두 유실' 조각을 되살리는 근거다.
  const cls = modalLabelClass(labels);
  const prefix = modalLabelPrefix(labels, cls);
  const nums = labels
    .filter((l) => isWellFormedLabel(l, cls, prefix))
    .map((l) => /[0-9]+$/.exec(l)?.[0] ?? "")
    .filter((n) => n);
  // 관측된 번호 자릿수 상한 — 접두 복원이 만들어 낼 수 있는 번호의 한계.
  const maxDigits = nums.reduce((a, n) => Math.max(a, n.length), 0);
  // 번호 폭이 자연히 늘어나는 쇼인가(Scene1 … Scene678)? 그렇다면 자릿수 차이를
  // 오독으로 보면 안 된다 — 멀쩡한 씬 수백 개가 오독 목록에 쌓인다(실기 321씬
  // 중 180행이 그랬다). 판정은 비율이 아니라 **자리 채움 0**으로 한다: 폭을
  // 고정한 쇼는 번호를 0으로 채우고(0010·050), 자연수로 세는 쇼는 절대 그러지
  // 않는다. 임계값이 없어 코퍼스가 작아도 흔들리지 않는다. 폭 고정 쇼에서는
  // 한 자리 빠진 것이 진짜 오독이므로 엄격한 템플릿을 그대로 쓴다.
  const padded = nums.some((n) => n.length > 1 && n.startsWith("0"));
  const widthVaries = !padded && new Set(nums.map((n) => n.length)).size > 1;
  const out: LabelAnomaly[] = [];
  labels.forEach((label, index) => {
    const toks = tokenizeSlate(label, delimiters);
    // 모양 템플릿만으로는 접두 오독을 못 잡는다 — 'Scehe651'·'Seene656'은
    // 자리 배치가 정상과 똑같아 그대로 통과했다(실기: 이런 행이 제안도 못 받고
    // 목록에 남았다). 이 쇼의 지배적 접두가 있으면 그것도 조건에 넣는다.
    const ok = widthVaries
      ? isWellFormedLabel(label, cls, prefix)
      : (toks.length === tpl.length
         && toks.every((t, i) => matchesShape(t, tpl[i] as string))
         && (!prefix || label.startsWith(prefix)));
    if (ok) return;
    const fix = reparse(label, tpl, delimiters);
    let suggestion = fix?.label ?? null;
    let confident = fix?.confident ?? false;
    if (suggestion == null && prefix && !isWellFormedLabel(label, cls, prefix)) {
      const repaired = repairWithPrefix(prefix, label, maxDigits);
      if (repaired && repaired !== label && isWellFormedLabel(repaired, cls, prefix)) {
        suggestion = repaired;
        // 번호가 맞는지는 접두만으로 알 수 없다 — 바로 옆 이웃이 같은 이름일
        // 때만 확신한다(그 씬의 정상 판독이 옆에 있다는 뜻). 아니면 노란
        // 제안으로 두어 사용자가 프레임을 보고 판단하게 한다.
        confident = repaired === labels[index - 1] || repaired === labels[index + 1];
      }
    }
    out.push({ index, label, suggestion, confident });
  });
  return out;
}

import type { BoundaryOk, SceneSegment } from "./videoApi";

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

// 한 씬 안에 두 씬이 붙어 있을 때(스캔이 그 컷을 못 잡은 경우) 나눈다. k는 팝업
// 카운터의 '프레임 k / n' — 지금 보는 프레임이 **뒤 구간의 첫 프레임**이 된다
// (In 트림 "여기부터"와 같은 약속이라 새로 배울 게 없다). 자를 시각도 In 트림과
// 같은 shiftBoundaryMs를 쓰므로 나눈 경계는 그 프레임에 In 트림을 건 것과 정확히
// 같다 — 다른 수식을 쓰면 익스포트 -ss snap-up과 어긋나 프레임이 하나 밀린다.
//
// 뒤 구간이 원래(읽어낸) 이름을 유지하고, 앞 구간에는 `_cut` 임시 이름이 붙는다.
// 두 줄이 같은 이름이면 목록에서 어느 쪽을 고쳐야 할지 알 수 없고, 익스포트 파일명도
// dedupe 접미사가 붙어 헷갈린다. 진짜 이름은 슬레이트를 읽어 붙이는 별도 단계
// (renameSegment)의 몫이다 — OCR은 비동기라 시점이 다르고, 못 읽으면 `_cut`이 남아
// 고쳐야 할 줄이 한눈에 보인다.
//
// k<=1이면 앞 구간이 0프레임이라 아무것도 하지 않는다(빈 구간은 익스포트가 0바이트
// 클립을 만든다).
export function splitSegment(
  segs: SceneSegment[], i: number, k: number, fps: number,
): SceneSegment[] {
  const cur = segs[i];
  if (!cur || k <= 1) return segs;
  const cutMs = shiftBoundaryMs(cur.start_ms, fps, Math.floor(k) - 1);
  if (cutMs <= cur.start_ms || cutMs >= cur.end_ms) return segs;
  const out = segs.slice();
  out.splice(i, 1,
    { ...cur, end_ms: cutMs, label: cutLabel(segs, cur.label) },
    { ...cur, start_ms: cutMs });
  return out;
}

// 나눈 앞 구간에 읽어낸 이름을 얹는다 — 단 그 자리가 아직 자리표시자일 때만.
//
// 슬레이트 읽기는 비동기라 그 사이 사용자가 되돌리거나 다른 편집을 했을 수 있다.
// 확인 없이 덮으면 엉뚱한 줄의 이름을 바꾼다. 또 이 함수는 '지금 상태'를 받아
// 계산해야 한다 — 분할 시점에 닫아둔 배열 위에서 이름을 바꾸면 그 배열이 그대로
// 되살아나 방금 나눈 줄이 통째로 사라진다(2026-07-28 실기 재현).
export function applySplitName(
  segs: SceneSegment[], i: number, placeholder: string, label: string,
): SceneSegment[] {
  if (segs[i]?.label !== placeholder) return segs;
  return renameSegment(segs, i, label);
}

// 나눈 앞 구간의 임시 이름. 이미 쓰이고 있으면 숫자를 늘린다 — 같은 씬을 여러 번
// 나누거나 이름을 안 고친 채 옆 씬을 또 나눠도 이름이 겹치지 않아야 한다.
function cutLabel(segs: SceneSegment[], base: string): string {
  const taken = new Set(segs.map((s) => s.label));
  const first = `${base}_cut`;
  if (!taken.has(first)) return first;
  for (let n = 2; n < 1000; n += 1) {
    const cand = `${first}${n}`;
    if (!taken.has(cand)) return cand;
  }
  return first;
}

// 앞뒤가 같은 라벨로 둘러싸인 짧은 구간을 흡수한다 — 씬/시퀀스는 번호가 바뀌었다
// 되돌아오지 않으므로, 동일 라벨 사이에 낀 짧은 구간은 OCR 판독 튐(오독)이다.
// 라벨 교정 없이 그냥 없앨 수 있다(교정 근거가 없는 접두 유실 오독도 처리). maxMs
// 이하만 흡수해 진짜 비단조(A|B|A에서 B가 긴 경우)는 보존한다. 반복 적용해
// 교대 오독(A|m|A|m|A)도 한 번에 정리한다.
export function absorbFlankedMisreads(
  segs: SceneSegment[], maxMs: number,
): SceneSegment[] {
  let cur = segs;
  for (let pass = 0; pass < 8; pass += 1) {
    const relabeled = cur.map((s, i) => {
      const prev = cur[i - 1];
      const next = cur[i + 1];
      if (prev && next && prev.label === next.label && s.label !== prev.label
          && (s.end_ms - s.start_ms) <= maxMs) {
        return { ...s, label: prev.label };
      }
      return s;
    });
    const merged = mergeAdjacentSameLabel(relabeled);
    if (merged.length === cur.length) return merged;  // 변화 없으면 끝
    cur = merged;
  }
  return cur;
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

// 구간의 '마지막 프레임' 시각(ms). 필름스트립 격자(2초)는 씬 경계와 무관해
// 머리/꼬리 혼입(±1~3프레임)을 격자 썸네일로는 검증할 수 없다 — 씬을 클릭하면
// 이 시각의 실제 프레임을 thumb-at으로 뽑아 꼬리를 본다. end_ms는 배타적 경계
// (다음 씬 시작)이라 한 프레임 이상 당겨야 다음 씬 첫 프레임을 집지 않는다
// (썸네일 추출 -ss가 snap-up이라 end 근처를 주면 다음 씬이 잡힌다). 1.5프레임
// 당겨 [1f,2f) 중앙을 노리면 fps 추정이 조금 어긋나도 항상 이 씬의 마지막
// 프레임에 떨어진다(경계는 넘지 않는다). 초단편 씬은 시작으로 클램프. 기본
// 24fps는 23.976 NTSC에서도 오차 <1프레임이라 프레임 정확하다.
export function segmentTailMs(startMs: number, endMs: number, fps = NTSC_FPS): number {
  const frameMs = 1000 / (fps > 0 ? fps : NTSC_FPS);
  // 익스포트 클립의 '실제 마지막 프레임'을 익스포트와 같은 공식으로 잡는다:
  //   -ss start(snap-up)로 첫 프레임 f0 = ceil(start/frameMs),
  //   -frames:v N (N=round((end-start)*fps/1000))로 N장 → 마지막 = f0+N-1.
  // 이전의 end-1.5f 어림값은 마지막보다 한 프레임 일렀다(실기 _0070: 189 vs 190).
  const f0 = Math.ceil(startMs / frameMs - 1e-6);
  const n = Math.max(1, Math.round((endMs - startMs) / frameMs));
  const lastFrame = f0 + n - 1;
  // 그 프레임을 -ss snap-up이 집도록 직전 프레임과의 간극중앙을 준다(머리와 대칭).
  return Math.max(startMs, Math.round((lastFrame - 0.5) * frameMs));
}

// fps 미상 시 가정값 — 자막 소스는 대부분 NTSC 23.976(24000/1001)이다. 서버가
// 측정 fps(video_fps)를 보내면 그 값을 쓴다(정확). 24로 가정하면 경계에서 프레임
// 인덱스가 1 어긋난다(실측: 28924ms가 24fps→695, 23.976fps→694).
export const NTSC_FPS = 24000 / 1001;

// 머리·꼬리 프레임을 팝업(HTML5 <video>)으로 크게 볼 때의 시킹 시각(ms).
// 서버 썸네일은 -ss(입력시킹) snap-up(PTS≥t)이라 경계 간극중앙 start_ms에서 그
// 씬 첫 프레임을 정확히 집는다. 그러나 <video>.currentTime=t는 t를 '포함'하는
// 프레임(=직전 프레임=이전 씬 마지막)을 보여줘 팝업만 한 프레임 앞으로 어긋난다
// (실기: _020 머리 클릭→_010_0180). 그래서 팝업은 '썸네일이 집은 그 프레임'의
// 표시구간 중앙으로 시킹한다 — 소스 fps로 프레임 인덱스를 잡아야 정확하다.
export function frameSeekMs(ms: number, fps = NTSC_FPS): number {
  const frameMs = 1000 / (fps > 0 ? fps : NTSC_FPS);
  const idx = Math.max(0, Math.ceil(ms / frameMs - 1e-6)); // -ss snap-up이 고르는 프레임
  return Math.round((idx + 0.5) * frameMs);                // 그 프레임 표시구간 중앙
}

// 인접 두 씬의 공유 경계를 정확히 deltaFrames 프레임만큼 옮긴 새 경계 시각(ms).
// 스캔이 못 잡는 디졸브/와이프에서 머리·꼬리에 붙은 프레임을 이웃 씬으로 넘길 때
// 쓴다. 경계는 export -ss(snap-up)가 '뒤 세그먼트 첫 프레임'을 집는 값이라, 그
// 프레임 인덱스 k=ceil(boundary/frameMs)에 delta를 더한 프레임의 간극중앙을 돌려준다
// → 새 경계로 잘라도 프레임 정확(±1프레임씩 이동, 프레임 수 보존). delta>0이면 경계가
// 뒤로(뒤 세그가 줄고 앞 세그가 늘어남), <0이면 앞으로.
export function shiftBoundaryMs(boundaryMs: number, fps: number, deltaFrames: number): number {
  const frameMs = 1000 / (fps > 0 ? fps : NTSC_FPS);
  const k = Math.ceil(boundaryMs / frameMs - 1e-6);  // export -ss가 집는 뒤 세그 첫 프레임
  return Math.max(0, Math.round((k + deltaFrames - 0.5) * frameMs));
}

// 특정 시각의 '프레임 번호'(1부터). HTML5 <video>는 currentTime t를 포함하는
// 프레임(floor(t·fps))을 보여주므로 그 인덱스에 +1 한 값이 사람이 세는 프레임
// 번호다(첫 프레임 = 1). 팝업 프레임 카운터·오류 프레임 입력 기준.
export function frameNumberAt(ms: number, fps = NTSC_FPS): number {
  const frameMs = 1000 / (fps > 0 ? fps : NTSC_FPS);
  return Math.max(1, Math.floor(ms / frameMs + 1e-6) + 1);
}

// 익스포트 클립 안에서의 프레임 번호(1부터)와 총 프레임 수. k는 이 구간의 몇 번째
// 프레임인지(머리=1 … 꼬리=n), n은 익스포트가 뽑는 프레임 수(segmentTailMs와 동일
// 수식: f0=ceil(start/frameMs), n=round((end-start)·fps/1000)). 오류난 프레임을
// '머리에서 k번째'로 읽으면 그대로 경계 교정 프레임 수가 된다. 범위를 벗어난 시각은
// [1,n]으로 클램프.
export function segFrameNumber(
  ms: number, startMs: number, endMs: number, fps = NTSC_FPS,
): { k: number; n: number } {
  const frameMs = 1000 / (fps > 0 ? fps : NTSC_FPS);
  const f0 = Math.ceil(startMs / frameMs - 1e-6);
  const n = Math.max(1, Math.round((endMs - startMs) / frameMs));
  const abs0 = Math.floor(ms / frameMs + 1e-6);
  const k = Math.min(n, Math.max(1, abs0 - f0 + 1));
  return { k, n };
}

// 편집 프로그램식 In/Out 트림 — 팝업 카운터의 '프레임 k / n'을 경계 이동 프레임
// 수로 바꾼다. In("여기부터")은 찍은 프레임을 이 씬의 첫 프레임으로 만들어 앞의
// k-1장을 이전 씬에 넘기고, Out("여기까지")은 마지막 프레임으로 만들어 뒤의 n-k장을
// 다음 씬에 넘긴다. 찍은 프레임은 항상 이 씬에 남으므로(양쪽 다 최소 1프레임 잔존)
// 빈 씬이 원리적으로 생기지 않는다 — nudgeBoundary의 클램프에 의존하지 않는다.
// 범위 밖 k는 segFrameNumber와 같이 [1,n]으로 클램프.
export function trimFrames(
  k: number, n: number,
): { inFrames: number; outFrames: number } {
  const total = Math.max(1, Math.floor(n));
  const cur = Math.min(total, Math.max(1, Math.floor(k)));
  return { inFrames: cur - 1, outFrames: total - cur };
}

// ── 목록 탐색(수백 줄) ────────────────────────────────────────────────────────
// 씬 모드는 구간이 400개를 넘어 스크롤로 특정 씬을 찾는 게 고통스럽다. 라벨 검색과
// 이전/다음 이동으로 스크롤을 대체한다.

// 검색어 정규화 — 대소문자·공백·구분자를 무시한다. 슬레이트를 눈으로 읽고 옮겨 칠 때
// "010_0230"·"010 0230"·"0100230"이 다 같은 씬을 가리켜야 한다(OCR 라벨의 구분자
// 표기가 작품마다 달라 사용자가 무엇을 칠지 예측할 수 없다).
const normalizeQuery = (s: string): string =>
  s.toLowerCase().replace(/[\s_\-/]/g, "");

export function matchesLabelQuery(label: string, query: string): boolean {
  const q = normalizeQuery(query);
  if (q.length === 0) return true;  // 빈 검색어 = 필터 없음
  return normalizeQuery(label).includes(q);
}

// 탭 필터(base: 오독/경계 탭의 인덱스, 없으면 null=전체)에 라벨 검색을 교차한다.
// 반환값은 원본 인덱스 기준 — 병합·이름수정 콜백이 인덱스를 쓰기 때문에 재번호를
// 매기면 엉뚱한 구간을 건드린다. 필터도 검색도 없으면 null(=전체)을 그대로 돌려준다.
export function filterIndices(
  labels: string[], base: number[] | null, query: string,
): number[] | null {
  const q = normalizeQuery(query);
  if (q.length === 0) return base;
  const pool = base ?? labels.map((_, i) => i);
  return pool.filter((i) => matchesLabelQuery(labels[i] ?? "", query));
}

// 경계오류 탭에 보일 구간 인덱스.
//
// 저장된 인덱스가 아니라 '현재 세그먼트의 라벨'로 다시 찾는다 — 병합·분할·이름수정
// 으로 목록이 바뀌어도 어긋나지 않고, 라벨이 사라진 구간은 자동으로 빠진다.
//
// 사용자가 '문제없음'으로 확인한 구간은 뺀다. 검사는 디졸브처럼 두 슬레이트가 겹쳐
// 보이는 구간을 혼입으로 잡는데 실제로는 경계가 맞는 경우가 있고, 400씬 검수는 여러
// 세션에 걸치므로 확인 결과가 남아야 한다. 단 확인 당시의 시작·끝과 지금이 다르면
// 확인표시를 무시한다 — 그 뒤에 경계를 고쳤다는 뜻이고, 바뀐 경계를 안 본 채로
// 숨기면 안 된다.
export function boundaryIssueIndices(
  issues: Array<{ label: string }>, segs: SceneSegment[], ok: BoundaryOk[],
): number[] {
  const idxOf = new Map(segs.map((s, i) => [s.label, i] as const));
  const okOf = new Map(ok.map((o) => [o.label, o] as const));
  const out: number[] = [];
  for (const issue of issues) {
    const i = idxOf.get(issue.label);
    if (i == null) continue;
    const seg = segs[i] as SceneSegment;
    const cleared = okOf.get(issue.label);
    if (cleared && cleared.start_ms === seg.start_ms
        && cleared.end_ms === seg.end_ms) continue;
    out.push(i);
  }
  return out;
}

function editDistance(a: string, b: string): number {
  // 라벨은 짧다(수십 자) — 단순 DP로 충분하다.
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i += 1) {
    const cur = [i];
    for (let j = 1; j <= b.length; j += 1) {
      cur[j] = Math.min(
        (prev[j] as number) + 1,
        (cur[j - 1] as number) + 1,
        (prev[j - 1] as number) + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
    }
    prev = cur;
  }
  return prev[b.length] as number;
}

// 라벨 모양에서 '숫자 자릿수'만 지운다 — 씬 번호는 1~3자리로 늘어나므로
// tokenShape 그대로면 정상 라벨이 서로 다른 모양이 된다(실기 321씬: U1L4D3 156,
// D2 85, D1 9). 글자 런 길이는 남긴다: 'Scenel311'(L5)처럼 글자가 하나 더 낀
// 오독을 정상(L4)과 구분해야 한다.
export function labelClassKey(label: string): string {
  return tokenShape(label).replace(/D\d+/g, "D");
}

// 코퍼스에서 가장 흔한 라벨 모양 = '정상'의 기준.
export function modalLabelClass(labels: string[]): string | null {
  return modeOf(labels.filter((l) => l).map(labelClassKey));
}

// 정상 모양 라벨들이 공유하는 접두("Scene") — 번호 앞의 글자 부분 중 최빈값.
//
// '최장 공통 접두'로 구하면 안 된다: 오독 한 건('Bene603')만 섞여도 공통 부분이
// 통째로 날아가 빈 문자열이 된다(실기 321씬에서 실제로 그랬다). 라벨마다 앞쪽
// 글자 런을 뽑아 최빈값을 고르면 소수의 오독이 다수를 못 이긴다.
export function modalLabelPrefix(labels: string[], cls: string | null): string {
  const heads = labels
    .filter((l) => l && (cls == null || labelClassKey(l) === cls))
    .map((l) => (/^[^0-9]+/.exec(l)?.[0] ?? ""))
    .filter((h) => h);
  const top = modeOf(heads);
  if (!top) return "";
  // 접두는 '이 쇼의 규칙'이라고 말할 수 있을 만큼 지배적일 때만 인정한다.
  // 접두가 여럿인 작품(AA001/BB002가 섞인 경우)에서 다수 접두를 규칙으로 삼으면
  // 소수 접두 라벨이 통째로 오독 취급된다.
  const share = heads.filter((h) => h === top).length / heads.length;
  return share >= 0.8 ? top : "";
}

// 이 쇼의 정상 라벨인가 — 모양(자릿수 무시)과 접두가 둘 다 맞아야 한다.
//
// 모양만으로는 부족하다: 'Seene9'·'Sdene94'는 자리 배치가 정상 라벨과 똑같아
// (글자+숫자) 통과해 버린다. 반대로 접두만 보면 'Scene,63'·'Scene7s'가 샌다.
export function isWellFormedLabel(
  label: string, cls: string | null, prefix: string,
): boolean {
  if (!label) return false;
  if (cls != null && labelClassKey(label) !== cls) return false;
  return !prefix || label.startsWith(prefix);
}

// 오독 구간을 어느 쪽 이웃에 병합해야 하는지 힌트.
//
// 필터(경계 오류·오독 탭)를 걸면 병합 대상인 '진짜 이웃'이 목록에서 사라져,
// 화면만 보고는 ◀/▶ 중 무엇을 눌러야 할지 알 수 없다(실기). 이웃 이름을 버튼에
// 적어 주는 것이 본체이고, 이 함수는 그 위에 얹는 힌트다 — 자동 적용하지 않는다.
//
// **병합은 언제나 '이웃의 이름'이 살아남는다**(이 구간은 흡수돼 사라진다).
// 그래서 깨진 이웃 쪽은 후보에서 뺀다 — 그쪽으로 추천하면 멀쩡한 이름이 지워진다
// (실기: 정상 'Scene678'에 '◀ 678'이 추천으로 떴다).
//
// 판정 순서: ① 양쪽 이웃이 같은 씬이면 어느 쪽이든 결과가 같다("both")
// ② 교정 제안(접두 복원 등)이 한쪽 이름과 정확히 일치하면 그쪽 — 코퍼스 근거라
// 글자수보다 세다 ③ 양쪽을 견줄 수 있을 때만 편집거리가 가까운 쪽. 한쪽만 후보면
// ②가 아닌 한 침묵한다 — 비교 대상이 없으면 "남은 쪽"이 정답이라는 근거가 없다
// (실기 'Scene': 앞 Scene44가 유일 후보였지만 정답은 뒤쪽 '45'였다). 거리가 라벨
// 길이에 비해 크면 역시 침묵한다 — 다른 씬을 "덜 틀린 쪽"으로 떠밀면 해롭다.
export function mergeNeighborHint(opts: {
  label: string;
  prev?: string | null;
  next?: string | null;
  suggestion?: string | null;
  // 정상 라벨의 기준(modalLabelClass·modalLabelPrefix). 이웃 자격과 '이 행이
  // 애초에 병합 대상인가'를 함께 심사한다.
  validClass?: string | null;
  validPrefix?: string;
}): "prev" | "next" | "both" | null {
  const { label, suggestion } = opts;
  const validClass = opts.validClass ?? null;
  const validPrefix = opts.validPrefix ?? "";
  // 멀쩡한 라벨에는 추천하지 않는다. 씬 번호는 이웃과 한두 글자 차이라(Scene19 vs
  // Scene18) 거리만 보면 정상 씬마다 추천이 떠 목록이 온통 초록이 된다(실기).
  // 병합은 이 구간을 지우는 조작이므로, 지워도 되는 조각에만 권한다.
  if (isWellFormedLabel(label, validClass, validPrefix)) return null;
  const ok = (l: string | null | undefined): string | null =>
    (l != null && isWellFormedLabel(l, validClass, validPrefix)) ? l : null;
  const prev = ok(opts.prev);
  const next = ok(opts.next);
  if (prev == null && next == null) return null;
  if (prev != null && next != null && prev === next) return "both";
  const want = suggestion?.trim() ? suggestion : label;
  if (prev === want && next !== want) return "prev";
  if (next === want && prev !== want) return "next";
  // 교정하면 멀쩡한데 양쪽 어느 이웃과도 다르면, 이건 흡수할 조각이 아니라
  // '이름만 고치면 되는 독립 씬'이다 — 병합을 권하면 씬 하나가 사라진다
  // (실기 'Scéne639': 이웃은 638·640이고 639는 이 줄에만 있다).
  if (suggestion?.trim()
      && isWellFormedLabel(suggestion, validClass, validPrefix)) return null;
  if (prev == null || next == null) return null;
  const dp = editDistance(want, prev);
  const dn = editDistance(want, next);
  if (dp === dn) return null;
  if (Math.min(dp, dn) > Math.max(2, Math.floor(want.length / 3))) return null;
  return dp < dn ? "prev" : "next";
}

// 서버가 돌려준 익스포트 경로에서 파일명만 뽑는다. 서버가 윈도우면 역슬래시,
// 맥/리눅스면 슬래시로 온다 — 클라 OS와 다를 수 있으므로 둘 다 자른다.
export function exportedFileName(serverPath: string): string {
  const parts = serverPath.split(/[\\/]/);
  return parts[parts.length - 1] ?? serverPath;
}

// 스캔 진척 판정 키 — 이 값이 바뀌지 않으면 '정체'로 센다.
//
// 판독 수(ocr_done)만 보면 안 된다: 스캔의 앞 구간(크롭·프레임 추출·컷 감지)은
// 카운터가 0에 머물고, 판독이 N/N에 닿은 뒤에도 재시도 단계(패딩 재판독·개별
// 시킹)가 한참 돈다. 실기에서 화면이 '판독 중… 2791/2791'에 굳은 채 서버는
// 멀쩡히 일하는데 200초 뒤 "스캔이 진행되지 않습니다"가 떴다. 서버가 함께
// 내려주는 stage_tick(산출물이 실제로 늘 때만 오르는 값)을 같이 본다 —
// 진짜로 멎으면 둘 다 안 변하므로 정체 감지는 그대로 살아 있다.
export function scanProgressKey(
  d: { ocr_done?: number; stage_tick?: number },
): string {
  return `${d.ocr_done ?? 0}:${d.stage_tick ?? 0}`;
}

// 팝업 검수 단축키 매핑(한 곳에서). 손을 옮기지 않고 한 씬을 훑도록 왼손 자리에
// 모았다: I/O=In/Out 트림, G/H=이전/다음 씬, [/]=머리로/꼬리로.
//
// 한글 IME가 켜져 있으면 e.key가 자모("ㅎ"/"ㅗ")나 "Process"로 와서 문자 비교만
// 으로는 단축키가 조용히 안 먹는다(Windows WebView2에서 특히, macOS도 동일) —
// 물리 키(e.code)를 함께 본다. 이 앱 사용자는 한글 입력 상태가 기본이다.
// 대괄호는 Shift 조합({ })도 같은 물리 키라 함께 받는다.
export type ScenePopupAction =
  | "trimIn" | "trimOut" | "split" | "prevScene" | "nextScene" | "toHead" | "toTail";

export function scenePopupAction(
  ev: { code?: string; key?: string },
): ScenePopupAction | null {
  const key = (ev.key ?? "").toLowerCase();
  if (ev.code === "KeyI" || key === "i") return "trimIn";
  if (ev.code === "KeyO" || key === "o") return "trimOut";
  if (ev.code === "KeyS" || key === "s") return "split";
  if (ev.code === "KeyG" || key === "g") return "prevScene";
  if (ev.code === "KeyH" || key === "h") return "nextScene";
  if (ev.code === "BracketLeft" || key === "[" || key === "{") return "toHead";
  if (ev.code === "BracketRight" || key === "]" || key === "}") return "toTail";
  return null;
}

// 보이는 목록에서 delta칸 이동한 원본 인덱스. 끝에서는 null(감싸지 않는다 —
// 400줄에서 갑자기 처음으로 튀면 위치 감각을 잃는다). 선택이 없으면 진행 방향의
// 첫 항목, 선택이 필터에서 빠진 상태면 그 방향의 가장 가까운 항목으로 간다.
export function stepVisibleIndex(
  visible: number[], current: number | null, delta: number,
): number | null {
  if (visible.length === 0) return null;
  if (current == null) return (delta > 0 ? visible[0] : visible[visible.length - 1]) ?? null;
  const pos = visible.indexOf(current);
  if (pos >= 0) {
    const next = pos + delta;
    return next >= 0 && next < visible.length ? visible[next] ?? null : null;
  }
  return delta > 0
    ? visible.find((i) => i > current) ?? null
    : [...visible].reverse().find((i) => i < current) ?? null;
}

// 개별 씬 익스포트가 다시 구울 구간들 — 고른 씬과 맞닿은 이웃. 경계를 옮기면 그 씬만
// 아니라 이웃의 프레임 수도 함께 바뀌므로(공유 경계), 고른 씬만 내보내면 이웃 mp4가 옛
// 경계로 남아 폴더가 정합을 잃는다. 목록 양끝은 클램프, 범위 밖 인덱스는 빈 배열.
export function neighborIndices(i: number, n: number): number[] {
  if (!Number.isFinite(i) || i < 0 || i >= n) return [];
  const out: number[] = [];
  for (let k = i - 1; k <= i + 1; k += 1) if (k >= 0 && k < n) out.push(k);
  return out;
}

// 익스포트 탐침 파일 이름. 접두사 `yeson_probe_`는 Rust(probe_file_write/remove의
// PROBE_PREFIX)와 서버(_PROBE_PREFIX)가 함께 지키는 계약이다 — Rust는 이 접두사가
// 아닌 경로를 거부하고(사용자 파일을 지울 통로를 막는다), 서버는 같은 이름으로
// 파일을 찾는다. 한쪽만 바꾸면 탐침이 조용히 실패해 같은 PC에서도 중계로 떨어진다.
export function probeFileName(token: string): string {
  return `yeson_probe_${token}.tmp`;
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
