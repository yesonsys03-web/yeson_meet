import { describe, expect, it } from "vitest";

import {
  PT_PER_IMAGE_PX,
  clampRectToPage,
  clientPointToPt,
  displayScale,
  hitTestPanel,
  ptToPx,
  pxToPt,
  rectToRel,
  rectToStyle,
  relToPoint,
  type ImageBox,
  type Rect,
} from "./pdfLabelGeom";

// 1008pt 폭 페이지를 120dpi로 구우면 1680px — 서버 `render_png(dpi=120)`와 같다.
const NATURAL_WIDTH = 1680;
const full: ImageBox = { left: 0, top: 0, width: NATURAL_WIDTH, naturalWidth: NATURAL_WIDTH };
// 창이 좁아 절반으로 축소돼 그려진 경우.
const half: ImageBox = { left: 40, top: 12, width: NATURAL_WIDTH / 2, naturalWidth: NATURAL_WIDTH };

describe("pdfLabelGeom", () => {
  it("120dpi 미리보기의 1px는 0.6pt다", () => {
    expect(PT_PER_IMAGE_PX).toBeCloseTo(0.6, 10);
    expect(displayScale(full)).toBe(1);
    expect(displayScale(half)).toBe(0.5);
  });

  it("클라이언트 좌표를 이미지 원점과 표시 배율을 보정해 pt로 바꾼다", () => {
    // 원본 배율: (600, 300)px → 360, 180pt
    expect(clientPointToPt(600, 300, full)).toEqual({ x: 360, y: 180 });
    // 절반 축소 + 오프셋: 화면상 (40+300, 12+150) 이 같은 지점이어야 한다.
    const p = clientPointToPt(340, 162, half);
    expect(p.x).toBeCloseTo(360, 6);
    expect(p.y).toBeCloseTo(180, 6);
  });

  it("pt→px→pt 왕복 오차가 0.01pt 미만이다", () => {
    for (const box of [full, half]) {
      for (const pt of [0, 0.6, 37.42, 279.2, 611.99]) {
        expect(Math.abs(pxToPt(ptToPx(pt, box), box) - pt)).toBeLessThan(0.01);
      }
    }
  });

  it("rect를 절대배치 CSS px로 바꾼다(퇴화 방지 최소 1px)", () => {
    const style = rectToStyle([60, 120, 160, 140], full);
    expect(style.left).toBeCloseTo(100, 6);
    expect(style.top).toBeCloseTo(200, 6);
    expect(style.width).toBeCloseTo(166.666, 2);
    expect(style.height).toBeCloseTo(33.333, 2);
    expect(rectToStyle([10, 10, 10, 10], full).width).toBe(1);
  });

  it("판넬 히트 테스트는 경계를 포함하고 밖이면 null이다", () => {
    // 실물 3단 기하(FL102·FL104 전 페이지 동일).
    const panels: Rect[] = [
      [38.1, 110.9, 340.2, 279.2],
      [353.5, 110.9, 655.6, 279.2],
      [668.9, 110.9, 971.0, 279.2],
    ];
    expect(hitTestPanel({ x: 200, y: 200 }, panels)).toBe(0);
    expect(hitTestPanel({ x: 400, y: 200 }, panels)).toBe(1);
    expect(hitTestPanel({ x: 700, y: 200 }, panels)).toBe(2);
    expect(hitTestPanel({ x: 38.1, y: 110.9 }, panels)).toBe(0);   // 경계 포함
    expect(hitTestPanel({ x: 345, y: 200 }, panels)).toBeNull();   // 칸 사이 여백
    expect(hitTestPanel({ x: 200, y: 500 }, panels)).toBeNull();   // 필드 영역
  });

  it("페이지 밖으로 나간 rect는 크기를 유지한 채 안으로 밀어 넣는다", () => {
    const page: [number, number] = [1008, 612];
    expect(clampRectToPage([980, 600, 1060, 620], page)).toEqual([928, 592, 1008, 612]);
    expect(clampRectToPage([-30, -10, 50, 10], page)).toEqual([0, 0, 80, 20]);
    // 이미 안에 있으면 그대로.
    expect(clampRectToPage([100, 100, 180, 120], page)).toEqual([100, 100, 180, 120]);
  });

  it("판넬 기준 정규화 좌표를 낸다(주소 저장 형식)", () => {
    const panel: Rect = [38.1, 110.9, 340.2, 279.2];
    const rel = rectToRel([38.1, 110.9, 100, 130], panel);
    expect(rel.x).toBeCloseTo(0, 6);
    expect(rel.y).toBeCloseTo(0, 6);
    const mid = rectToRel([189.15, 195.05, 200, 210], panel);
    expect(mid.x).toBeCloseTo(0.5, 3);
    expect(mid.y).toBeCloseTo(0.5, 3);
  });

  it("정규화 좌표를 pt로 되돌린다(클릭 지점 표시)", () => {
    const panel: Rect = [38.1, 110.9, 340.2, 279.2];
    // 왕복해도 제자리 — 표시용 마커가 저장될 주소와 어긋나면 안 된다.
    for (const rel of [[0, 0], [0.5, 0.5], [1, 1], [0.23, 0.77]] as [number, number][]) {
      const pt = relToPoint(panel, rel);
      const back = rectToRel([pt.x, pt.y, pt.x + 1, pt.y + 1], panel);
      expect(back.x).toBeCloseTo(rel[0], 6);
      expect(back.y).toBeCloseTo(rel[1], 6);
    }
    expect(relToPoint(panel, [0, 0])).toEqual({ x: 38.1, y: 110.9 });
  });
});
