// 라벨 편집기의 좌표 변환 — **DOM 없이** 테스트할 수 있게 순수 함수로 격리한다.
//
// pt가 유일한 진실이다. 서버는 원본 기준 pt(원점 좌상단)만 주고받고, 화면
// 픽셀은 여기서 파생한다. 반대로 하면(px를 저장하면) 창 크기나 미리보기 dpi가
// 바뀌는 순간 저장된 위치가 전부 어긋난다.
//
// ⚠ 실제 오차가 생기는 지점은 `clientX/Y → 이미지 원점 보정 → 표시 배율 → pt`
// 사슬 전체다. 그 사슬을 컴포넌트 글루에 두면 AC6의 ±0.6pt 단언이 공허해지므로
// (이미 변환된 값에 대해서만 단언하게 된다) **사슬 전체를 이 파일에 둔다.**

/** 서버가 미리보기 PNG를 굽는 해상도(`pdf_jobs.py`의 `render_png(dpi=120)`). */
export const PDF_PREVIEW_DPI = 120;

/** 미리보기 1px가 몇 pt인가 — 120dpi에서 0.6pt. */
export const PT_PER_IMAGE_PX = 72 / PDF_PREVIEW_DPI;

export type Rect = [number, number, number, number];
export type PointPt = { x: number; y: number };

/** 화면에 그려진 이미지의 기하 — `getBoundingClientRect()` + `naturalWidth`. */
export type ImageBox = {
  left: number;
  top: number;
  width: number;
  naturalWidth: number;
};

/** 이미지가 화면에서 축소/확대된 비율(1 = 원본 픽셀 그대로). */
export function displayScale(box: ImageBox): number {
  return box.width > 0 && box.naturalWidth > 0 ? box.width / box.naturalWidth : 1;
}

/**
 * 마우스 클라이언트 좌표 → 문서 pt.
 *
 * 세 단계를 한 함수에 모은다: 이미지 원점 보정 → 표시 배율 되돌리기 →
 * 미리보기 dpi로 pt 환산. 하나라도 밖에 있으면 그 부분은 테스트가 못 잡는다.
 */
export function clientPointToPt(
  clientX: number, clientY: number, box: ImageBox,
): PointPt {
  const scale = displayScale(box);
  return {
    x: ((clientX - box.left) / scale) * PT_PER_IMAGE_PX,
    y: ((clientY - box.top) / scale) * PT_PER_IMAGE_PX,
  };
}

/** pt → 화면 px(길이). 위치가 아니라 **길이** 변환이라 원점 보정이 없다. */
export function ptToPx(valuePt: number, box: ImageBox): number {
  return (valuePt / PT_PER_IMAGE_PX) * displayScale(box);
}

/** 화면 px(길이) → pt. `ptToPx`의 역함수. */
export function pxToPt(valuePx: number, box: ImageBox): number {
  return (valuePx / displayScale(box)) * PT_PER_IMAGE_PX;
}

/** 문서 rect(pt) → 이미지 위에 절대배치할 CSS 값(px). */
export function rectToStyle(rect: Rect, box: ImageBox): {
  left: number; top: number; width: number; height: number;
} {
  const [x0, y0, x1, y1] = rect;
  return {
    left: ptToPx(x0, box),
    top: ptToPx(y0, box),
    width: Math.max(1, ptToPx(x1 - x0, box)),
    height: Math.max(1, ptToPx(y1 - y0, box)),
  };
}

/**
 * 이 점이 들어가는 판넬의 인덱스 — 없으면 null.
 *
 * 자동 항목의 판넬 번호는 서버가 굽는 동안 계산하지 않는다(1037페이지에서
 * 그 계산만 19초였다). 편집 화면은 현재 페이지의 판넬을 이미 받아 두므로
 * 여기서 파생하는 편이 공짜다.
 */
export function hitTestPanel(point: PointPt, panels: Rect[]): number | null {
  for (let i = 0; i < panels.length; i += 1) {
    const panel = panels[i];
    if (!panel) continue;
    const [x0, y0, x1, y1] = panel;
    if (point.x >= x0 && point.x <= x1 && point.y >= y0 && point.y <= y1) return i;
  }
  return null;
}

/** rect의 **크기를 유지한 채** 페이지 안으로 밀어 넣는다. */
export function clampRectToPage(rect: Rect, pageSize: [number, number]): Rect {
  const [pw, ph] = pageSize;
  const w = rect[2] - rect[0];
  const h = rect[3] - rect[1];
  const x0 = Math.min(Math.max(0, rect[0]), Math.max(0, pw - w));
  const y0 = Math.min(Math.max(0, rect[1]), Math.max(0, ph - h));
  return [round2(x0), round2(y0), round2(x0 + w), round2(y0 + h)];
}

/** 판넬 안에서의 정규화 좌표(0~1) — 서버가 저장하는 주소 형식. */
export function rectToRel(rect: Rect, panel: Rect): PointPt {
  return {
    x: (rect[0] - panel[0]) / Math.max(1e-6, panel[2] - panel[0]),
    y: (rect[1] - panel[1]) / Math.max(1e-6, panel[3] - panel[1]),
  };
}

/** `rectToRel`의 역 — 정규화 주소를 다시 pt로. 클릭 지점 표시에 쓴다. */
export function relToPoint(panel: Rect, rel: [number, number]): PointPt {
  return {
    x: panel[0] + rel[0] * (panel[2] - panel[0]),
    y: panel[1] + rel[1] * (panel[3] - panel[1]),
  };
}

export function round2(v: number): number {
  return Math.round(v * 100) / 100;
}
