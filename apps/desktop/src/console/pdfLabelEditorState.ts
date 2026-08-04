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
