import { describe, expect, it } from "vitest";

import {
  dragCommitRect,
  editorReducer,
  initialEditorState,
  newLabelRect,
  resolveSubmitText,
  rowsOnPage,
  type EditorState,
  type LabelRow,
} from "./pdfLabelEditorState";
import type { Rect } from "./pdfLabelGeom";

function row(over: Partial<LabelRow> = {}): LabelRow {
  return {
    id: "a1", origin: "auto", kind: "panel_label", page: 700,
    panel_index: null, rect: [10, 10, 90, 30], fontsize: 10,
    source_text: "IN", text: "들어온다", edited: false, editable: true,
    ...over,
  };
}

const PAGES = 1037;

describe("editorReducer", () => {
  it("행을 누르면 그 페이지로 점프하면서 그 라벨이 선택된다(AC2)", () => {
    const next = editorReducer(initialEditorState,
      { type: "selectRow", row: row({ id: "x9", page: 712 }) }, PAGES);
    expect(next.page).toBe(712);
    expect(next.selectedId).toBe("x9");
  });

  it("페이지 이동은 문서 범위를 벗어나지 않고 선택을 푼다", () => {
    const at: EditorState = { ...initialEditorState, page: 5, selectedId: "keep" };
    expect(editorReducer(at, { type: "stepPage", delta: -10 }, PAGES).page).toBe(0);
    expect(editorReducer(at, { type: "gotoPage", page: 99999 }, PAGES).page).toBe(1036);
    expect(editorReducer(at, { type: "stepPage", delta: 1 }, PAGES).selectedId).toBeNull();
  });

  it("필터·검색이 바뀌면 페이징을 처음으로 되돌린다", () => {
    const deep: EditorState = { ...initialEditorState, offset: 400 };
    expect(editorReducer(deep, { type: "setKind", kind: "all" }, PAGES).offset).toBe(0);
    expect(editorReducer(deep, { type: "setQuery", query: "행크" }, PAGES).offset).toBe(0);
  });

  it("페이지가 0장이어도 음수로 가지 않는다", () => {
    expect(editorReducer(initialEditorState, { type: "gotoPage", page: 3 }, 0).page).toBe(0);
  });
});

describe("resolveSubmitText", () => {
  it("한글 칸이 비면 해독 미리보기가 저장된다", () => {
    expect(resolveSubmitText(["들어온다"], "")).toBe("들어온다");
    expect(resolveSubmitText(["좀비", "파티광1"], "  ")).toBe("좀비\n파티광1");
  });

  it("한글 칸이 채워지면 사람 값이 이긴다(AC4)", () => {
    expect(resolveSubmitText(["들어온다"], "안으로")).toBe("안으로");
  });

  it("해독도 실패하고 한글도 비면 저장할 것이 없다", () => {
    expect(resolveSubmitText(null, "")).toBe("");
    expect(resolveSubmitText([], "")).toBe("");
  });

  it("해독이 실패해도 사람이 직접 치면 저장된다", () => {
    expect(resolveSubmitText(null, "카메라 가이드")).toBe("카메라 가이드");
  });
});

describe("dragCommitRect", () => {
  const page: [number, number] = [1008, 612];

  it("크기를 유지한 채 delta만큼 옮긴다(AC6)", () => {
    expect(dragCommitRect([100, 100, 180, 120], 30, -25, page))
      .toEqual([130, 75, 210, 95]);
  });

  it("페이지 밖으로 나가면 안으로 밀어 넣는다", () => {
    const out = dragCommitRect([960, 580, 1040, 600], 100, 100, page);
    expect(out[2]).toBeLessThanOrEqual(1008);
    expect(out[3]).toBeLessThanOrEqual(612);
    expect(out[2] - out[0]).toBeCloseTo(80, 6);   // 크기 유지
  });

  it("120dpi 1px(=0.6pt) 단위의 미세 이동도 반영된다", () => {
    const moved = dragCommitRect([100, 100, 180, 120], 0.6, 0, page);
    expect(moved[0]).toBeCloseTo(100.6, 6);
  });
});

describe("newLabelRect / rowsOnPage", () => {
  it("새 라벨의 기본 크기는 판넬 폭의 40%와 줄 수 기준 높이다", () => {
    const panel: Rect = [38.1, 110.9, 340.2, 279.2];
    const r = newLabelRect(panel, 100, 150, 10, 1);
    expect(r[2] - r[0]).toBeCloseTo(120.84, 2);
    expect(r[3] - r[1]).toBeCloseTo(12.5, 2);
    expect(newLabelRect(panel, 100, 150, 10, 2)[3] - 150).toBeCloseTo(25, 2);
  });

  it("현재 페이지의 행만 오버레이로 그린다", () => {
    const rows = [row({ id: "a", page: 1 }), row({ id: "b", page: 2 }),
      row({ id: "c", page: 1 })];
    expect(rowsOnPage(rows, 1).map((r) => r.id)).toEqual(["a", "c"]);
  });
});
