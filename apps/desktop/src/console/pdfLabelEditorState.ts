// 라벨 편집기의 **상태 전이와 제출값 결정**을 순수 함수로 뽑는다.
//
// 이 리포에는 컴포넌트 테스트 인프라가 없다(테스트 24개가 전부 node 환경 순수
// 로직이고 `.test.tsx`는 0개다). jsdom을 들이면 이 기능과 무관한 표면이
// 넓어지므로, 리포 관례(`sceneSplitLogic.ts`)대로 판정 로직만 떼어내 잠근다.
// 컴포넌트는 이 함수들을 통과시키기만 한다.

import { clampRectToPage, round2, type Rect } from "./pdfLabelGeom";

export type LabelRow = {
  id: string;
  origin: "auto" | "manual";
  kind: string;
  page: number;
  panel_index: number | null;
  rect: Rect;
  fontsize: number;
  source_text: string;
  text: string;
  edited: boolean;
  editable: boolean;
};

export type EditorState = {
  page: number;
  selectedId: string | null;
  kind: "panel_label" | "all";
  query: string;
  offset: number;
};

export const initialEditorState: EditorState = {
  page: 0,
  selectedId: null,
  kind: "panel_label",
  query: "",
  offset: 0,
};

export type EditorAction =
  | { type: "selectRow"; row: LabelRow }
  | { type: "gotoPage"; page: number }
  | { type: "stepPage"; delta: number }
  | { type: "setKind"; kind: "panel_label" | "all" }
  | { type: "setQuery"; query: string }
  | { type: "setOffset"; offset: number }
  | { type: "clearSelection" };

/**
 * 상태 전이. `pageCount`는 페이지 이동을 문서 안으로 가두는 데만 쓴다.
 *
 * 목록표 행을 누르면 **그 페이지로 점프하면서 그 라벨이 선택**되는 것이 AC2다.
 * 두 가지가 한 전이로 일어나야 "눌렀는데 페이지만 바뀌고 선택은 안 됨" 같은
 * 중간 상태가 생기지 않는다.
 */
export function editorReducer(
  state: EditorState, action: EditorAction, pageCount: number,
): EditorState {
  const clampPage = (p: number) =>
    Math.max(0, Math.min(Math.max(0, pageCount - 1), p));
  switch (action.type) {
    case "selectRow":
      return { ...state, page: clampPage(action.row.page), selectedId: action.row.id };
    case "gotoPage":
      return { ...state, page: clampPage(action.page), selectedId: null };
    case "stepPage":
      return { ...state, page: clampPage(state.page + action.delta), selectedId: null };
    // 필터·검색이 바뀌면 페이징을 처음으로 되돌린다 — 안 그러면 결과가 줄었을
    // 때 빈 페이지에 머문다.
    case "setKind":
      return { ...state, kind: action.kind, offset: 0 };
    case "setQuery":
      return { ...state, query: action.query, offset: 0 };
    case "setOffset":
      return { ...state, offset: Math.max(0, action.offset) };
    case "clearSelection":
      return { ...state, selectedId: null };
    default:
      return state;
  }
}

/**
 * 저장할 최종 텍스트 — AC4의 규칙.
 *
 * 사람이 한글 칸을 비워 두면 해독 미리보기가 그대로 저장되고, 채워 두면
 * **사람 값이 이긴다.** 해독이 실패했는데(`decoded == null`) 한글도 비어 있으면
 * 저장할 게 없다는 뜻이라 빈 문자열을 돌려주고, 부르는 쪽이 제출을 막는다.
 */
export function resolveSubmitText(
  decoded: string[] | null, koreanOverride: string,
): string {
  const manual = koreanOverride.trim();
  if (manual) return manual;
  return decoded && decoded.length ? decoded.join("\n") : "";
}

/**
 * 드래그를 끝냈을 때 서버로 보낼 rect(pt).
 *
 * 이동 중에는 서버를 부르지 않는다 — pointerup에서 **한 번만** PATCH한다.
 * 크기는 유지하고 위치만 옮기며, 페이지 밖으로는 나가지 않는다.
 */
export function dragCommitRect(
  startRect: Rect, deltaXPt: number, deltaYPt: number,
  pageSize: [number, number],
): Rect {
  const moved: Rect = [
    startRect[0] + deltaXPt, startRect[1] + deltaYPt,
    startRect[2] + deltaXPt, startRect[3] + deltaYPt,
  ];
  return clampRectToPage(moved, pageSize);
}

/** 새 수동 라벨의 기본 rect — 판넬 안에서 클릭한 지점을 좌상단으로. */
export function newLabelRect(
  panel: Rect, pointX: number, pointY: number, fontsize: number, lines: number,
): Rect {
  const w = round2((panel[2] - panel[0]) * 0.4);
  const h = round2(fontsize * Math.max(1, lines) * 1.25);
  return clampRectToPage([pointX, pointY, pointX + w, pointY + h],
    [panel[2], panel[3]]);
}

/** 이 행이 지금 화면(현재 페이지)에 보이는가 — 오버레이 그리기 판정. */
export function rowsOnPage(rows: LabelRow[], page: number): LabelRow[] {
  return rows.filter((r) => r.page === page);
}

/**
 * 주석이 **하나도 없는** 페이지들 — 번역 누락을 찾는 사람이 훑어야 할 곳.
 *
 * 왜 목록 필터가 아니라 이것인가(FL104_Orev 113p 실측): 번역이 안 된 라벨은
 * `refine_ko`가 그 줄을 지워서 주석이 **아예 안 만들어지고**, OCR이 못 읽은
 * 손글씨는 애초에 블록이 없다. 그래서 "번역 안 된 항목"으로 목록을 좁히면
 * 0건이 뜬다(그 문서에서 영문이 그대로 찍힌 항목 = 0). 누락은 목록 **안**이
 * 아니라 목록에 **없는 페이지**로 나타난다 — 그 문서에서 주석 0개 페이지가
 * 69/113장이었다.
 *
 * 판정은 넘겨준 `rows` 기준이다. 편집기는 종류 필터를 통과한 행을 넘기므로
 * `판넬 라벨`을 보고 있으면 "판넬 라벨이 없는 페이지"를, `전체`면 "주석이
 * 아무것도 없는 페이지"를 돌게 된다 — 필터가 목록·화면·순회를 함께 지배한다.
 *
 * 표지처럼 원래 칸이 없는 페이지도 섞인다(실측 113장 중 1장). 그건 판넬
 * 정보가 있어야 걸러지는데 클라이언트는 현재 페이지 것만 받으므로, 걸러내는
 * 대신 사람이 넘기게 둔다 — 조용히 빼면 진짜 누락도 같이 빠질 위험이 있다.
 */
export function pagesWithoutRows(rows: LabelRow[], pageCount: number): number[] {
  const seen = new Set(rows.map((r) => r.page));
  const out: number[] = [];
  for (let p = 0; p < pageCount; p += 1) if (!seen.has(p)) out.push(p);
  return out;
}

/**
 * `from`에서 `delta` 방향으로 가장 가까운 후보 페이지. 없으면 `null`.
 *
 * 현재 페이지 자신은 건너뛴다 — 빈 페이지에 서서 `다음`을 눌렀는데 제자리면
 * 버튼이 고장 난 것처럼 보인다. 끝에서는 되돌지 않고 `null`(버튼 비활성)이다.
 */
export function nextPageWithout(
  pages: number[], from: number, delta: 1 | -1,
): number | null {
  const ahead = delta > 0
    ? pages.filter((p) => p > from)
    : pages.filter((p) => p < from).reverse();
  return ahead.length ? ahead[0] as number : null;
}
