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
  | "trimIn" | "trimOut" | "prevScene" | "nextScene" | "toHead" | "toTail";

export function scenePopupAction(
  ev: { code?: string; key?: string },
): ScenePopupAction | null {
  const key = (ev.key ?? "").toLowerCase();
  if (ev.code === "KeyI" || key === "i") return "trimIn";
  if (ev.code === "KeyO" || key === "o") return "trimOut";
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
