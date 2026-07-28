import { describe, expect, it } from "vitest";
import { absorbFlankedMisreads, anomalousLabels, applyFixes, confidentFixes, formatMs, frameNumberAt, frameSeekMs, mergeAdjacentSameLabel, regionFromDrag, labelTemplate, mergeSegment, NTSC_FPS, previewLabel, renameSegment, segFrameNumber, segmentTailMs, segmentThumbRange, shiftBoundaryMs, suggestLabelFix, tokenShape, tokenizeSlate, trimFrames, neighborIndices, matchesLabelQuery, filterIndices, stepVisibleIndex, scenePopupAction, scanProgressKey, mergeNeighborHint, labelClassKey, modalLabelClass, modalLabelPrefix, isWellFormedLabel, exportedFileName, probeFileName, probeToken, upsertBoundaryOk, splitSegment, applySplitName, boundaryIssueIndices } from "./sceneSplitLogic";

describe("tokenizeSlate", () => {
  it("splits underscore slate", () => {
    expect(tokenizeSlate("HH0307_020_0150_AC_v01")).toEqual(
      ["HH0307", "020", "0150", "AC", "v01"]);
  });
  it("keeps spaces inside fields by default (no space delimiter)", () => {
    // 기본 구분자는 `_`,`-`만 — 공백은 필드 안에 남는다. OCR이 "Seq 11B"처럼
    // 공백을 읽어도 토큰 인덱스가 밀리지 않는다(실기 관측 대응).
    expect(tokenizeSlate("Seq 07_S08 - Panel 3")).toEqual(
      ["Seq 07", "S08", "Panel 3"]);
    expect(tokenizeSlate("Seq 11B_S19-Panel3")).toEqual(
      ["Seq 11B", "S19", "Panel3"]);
  });
  it("splits on space too when explicitly requested", () => {
    expect(tokenizeSlate("Seq 07_S08 - Panel 3", ["_", " ", "-"])).toEqual(
      ["Seq", "07", "S08", "Panel", "3"]);
  });
});

describe("previewLabel", () => {
  it("joins prefix through upto index with underscore", () => {
    const t = ["HH0307", "020", "0150", "AC", "v01"];
    expect(previewLabel(t, 1)).toBe("HH0307_020");
    expect(previewLabel(t, 2)).toBe("HH0307_020_0150");
  });
});

describe("formatMs", () => {
  it("formats mm:ss", () => {
    expect(formatMs(23000)).toBe("0:23");
    expect(formatMs(112000)).toBe("1:52");
  });
});

describe("previewLabel whitespace normalization", () => {
  it("squashes OCR space blips so labels are stable", () => {
    expect(previewLabel(["Seq 01B", "S19"], 0)).toBe("Seq01B");
    expect(previewLabel(["Seq01B", "S19"], 0)).toBe("Seq01B");
  });
});

describe("segment editing", () => {
  const segs = [
    { label: "Seq12B", start_ms: 40000, end_ms: 88000 },
    { label: "VAL", start_ms: 88000, end_ms: 90000 },   // OCR 노이즈
    { label: "Seq13", start_ms: 90000, end_ms: 108000 },
  ];
  it("merges a bad segment into the previous one", () => {
    const out = mergeSegment(segs, 1, "prev");
    expect(out.map((s) => s.label)).toEqual(["Seq12B", "Seq13"]);
    expect(out[0]).toEqual({ label: "Seq12B", start_ms: 40000, end_ms: 90000 });
  });
  it("merges a bad segment into the next one", () => {
    const out = mergeSegment(segs, 1, "next");
    expect(out.map((s) => s.label)).toEqual(["Seq12B", "Seq13"]);
    expect(out[1]).toEqual({ label: "Seq13", start_ms: 88000, end_ms: 108000 });
  });
  it("renames a segment", () => {
    expect(renameSegment(segs, 1, "Seq12C")[1]?.label).toBe("Seq12C");
  });
  it("is a no-op at the edges / out of range", () => {
    expect(mergeSegment(segs, 0, "prev")).toEqual(segs);
    expect(mergeSegment(segs, 2, "next")).toEqual(segs);
    expect(mergeSegment(segs, 9, "prev")).toEqual(segs);
  });
});

describe("regionFromDrag", () => {
  const box = { left: 100, top: 50, width: 640, height: 360 };
  it("converts a drag on the displayed frame to frame-relative fractions", () => {
    // 표시 크기와 원본 해상도가 달라도 비율이라 그대로 쓸 수 있다.
    expect(regionFromDrag({ x: 132, y: 68 }, { x: 452, y: 104 }, box)).toEqual(
      { x: 0.05, y: 0.05, w: 0.5, h: 0.1 });
  });
  it("normalizes a drag made in any direction", () => {
    // 오른쪽아래→왼쪽위로 끌어도 같은 사각형이어야 한다.
    expect(regionFromDrag({ x: 452, y: 104 }, { x: 132, y: 68 }, box)).toEqual(
      { x: 0.05, y: 0.05, w: 0.5, h: 0.1 });
  });
  it("clamps a drag that leaves the frame", () => {
    const r = regionFromDrag({ x: -50, y: -20 }, { x: 9999, y: 9999 }, box);
    expect(r).toEqual({ x: 0, y: 0, w: 1, h: 1 });
  });
  it("returns null for a click without meaningful drag", () => {
    expect(regionFromDrag({ x: 200, y: 100 }, { x: 202, y: 101 }, box)).toBeNull();
  });
});

describe("tokenShape", () => {
  it("summarizes a token as runs of char class + length", () => {
    expect(tokenShape("040")).toBe("D3");
    expect(tokenShape("HH0307")).toBe("U2D4");
    expect(tokenShape("v01")).toBe("L1D2");
    expect(tokenShape("0400080")).toBe("D7");
  });
});

describe("labelTemplate", () => {
  it("takes the modal token count and modal shape per position", () => {
    // 대다수가 HH0307_040_0060 꼴, 하나만 오독(구분자 유실)
    const labels = ["HH0307_040_0060", "HH0307_040_0090",
                    "HH0307_0400080_ACV01", "HH0307_040_0110"];
    expect(labelTemplate(labels)).toEqual(["U2D4", "D3", "D4"]);
  });
  it("returns null when there is no usable majority", () => {
    expect(labelTemplate([])).toBeNull();
  });
});

describe("suggestLabelFix", () => {
  const tpl = ["U2D4", "D3", "D4"];
  it("re-splits a merged-token misread using the modal template", () => {
    // 실기: OCR이 언더스코어를 놓쳐 040_0080 → 0400080, AC_V01 → ACV01
    expect(suggestLabelFix("HH0307_0400080_ACV01", tpl)).toBe("HH0307_040_0080");
  });
  it("returns null for labels that already match the template", () => {
    expect(suggestLabelFix("HH0307_040_0060", tpl)).toBeNull();
  });
  it("returns null when the characters cannot fill the template", () => {
    expect(suggestLabelFix("HH0307_04", tpl)).toBeNull();
    expect(suggestLabelFix("VAL", tpl)).toBeNull();
  });
});

describe("anomalousLabels", () => {
  it("flags only the misread rows and pairs them with a suggestion", () => {
    const labels = ["HH0307_040_0060", "HH0307_040_0090",
                    "HH0307_0400080_ACV01", "HH0307_040_0110"];
    expect(anomalousLabels(labels)).toEqual([
      { index: 2, label: "HH0307_0400080_ACV01",
        suggestion: "HH0307_040_0080", confident: true },
    ]);
  });
  it("still reports an anomaly when no suggestion can be derived", () => {
    const labels = ["HH0307_040_0060", "HH0307_040_0090", "VAL",
                    "HH0307_040_0110"];
    expect(anomalousLabels(labels)).toEqual([
      { index: 2, label: "VAL", suggestion: null, confident: false },
    ]);
  });
  it("marks a suggestion unconfident when a digit is left over", () => {
    // 실기: HH0307_07510040_AC — 숫자가 8자리라 어디서 끊을지 모호하다(075|1004로
    // 채우면 '0'이 남는다). 자동 적용 대상에서 빼고 사람이 보게 한다.
    const labels = ["HH0307_075_0040", "HH0307_07510040_AC", "HH0307_075_0050"];
    expect(anomalousLabels(labels)).toEqual([
      { index: 1, label: "HH0307_07510040_AC",
        suggestion: "HH0307_075_1004", confident: false },
    ]);
  });
  it("returns nothing when every label matches the template", () => {
    expect(anomalousLabels(["HH0307_040_0060", "HH0307_040_0090"])).toEqual([]);
  });
});

describe("absorbFlankedMisreads", () => {
  it("absorbs a short run flanked by identical labels (definite misread)", () => {
    // 시퀀스A | 오독 | 시퀀스A — 시퀀스는 바뀌었다 되돌아오지 않으니 오독 확정.
    const segs = [
      { label: "HH_010", start_ms: 0, end_ms: 40000 },
      { label: "HH0100160_AC", start_ms: 40000, end_ms: 40500 },  // 0.5s 오독
      { label: "HH_010", start_ms: 40500, end_ms: 90000 },
    ];
    expect(absorbFlankedMisreads(segs, 5000)).toEqual([
      { label: "HH_010", start_ms: 0, end_ms: 90000 },
    ]);
  });
  it("handles alternating misreads (A|m|A|m|A) in one pass", () => {
    const segs = [
      { label: "A", start_ms: 0, end_ms: 1000 },
      { label: "m1", start_ms: 1000, end_ms: 1500 },
      { label: "A", start_ms: 1500, end_ms: 2000 },
      { label: "m2", start_ms: 2000, end_ms: 2500 },
      { label: "A", start_ms: 2500, end_ms: 3000 },
    ];
    expect(absorbFlankedMisreads(segs, 5000)).toEqual([
      { label: "A", start_ms: 0, end_ms: 3000 },
    ]);
  });
  it("keeps a long flanked run (possible real non-monotonic)", () => {
    const segs = [
      { label: "A", start_ms: 0, end_ms: 1000 },
      { label: "B", start_ms: 1000, end_ms: 20000 },  // 19s — 진짜일 수 있음
      { label: "A", start_ms: 20000, end_ms: 21000 },
    ];
    expect(absorbFlankedMisreads(segs, 5000)).toEqual(segs);
  });
});

describe("mergeAdjacentSameLabel", () => {
  it("merges consecutive segments with the same label into one span", () => {
    // 씬 한가운데 짧은 오독이 씬을 쪼갠 뒤 교정하면 같은 라벨이 인접한다 —
    // 이들을 시간축 이어 하나로 합친다.
    const segs = [
      { label: "HH_010_0210", start_ms: 0, end_ms: 1000 },
      { label: "HH_010_0210", start_ms: 1000, end_ms: 1500 },  // 교정된 오독
      { label: "HH_010_0210", start_ms: 1500, end_ms: 3000 },
      { label: "HH_010_0220", start_ms: 3000, end_ms: 4000 },
    ];
    expect(mergeAdjacentSameLabel(segs)).toEqual([
      { label: "HH_010_0210", start_ms: 0, end_ms: 3000 },
      { label: "HH_010_0220", start_ms: 3000, end_ms: 4000 },
    ]);
  });
  it("leaves non-adjacent same labels alone (non-monotonic slates)", () => {
    const segs = [
      { label: "A", start_ms: 0, end_ms: 1000 },
      { label: "B", start_ms: 1000, end_ms: 2000 },
      { label: "A", start_ms: 2000, end_ms: 3000 },
    ];
    expect(mergeAdjacentSameLabel(segs)).toEqual(segs);
  });
});

describe("confidentFixes / applyFixes", () => {
  const segs = [
    { label: "HH0307_040_0060", start_ms: 0, end_ms: 1000 },
    { label: "HH0307_0400080_ACV01", start_ms: 1000, end_ms: 2000 },
    { label: "HH0307_07510040_AC", start_ms: 2000, end_ms: 3000 },
    { label: "HH0307_040_0110", start_ms: 3000, end_ms: 4000 },
  ];
  it("lists before→after only for confident suggestions", () => {
    // 애매한 제안(숫자 잔여)은 미리보기 목록에도 넣지 않는다 — 일괄 적용 대상이
    // 아니므로 행별 버튼으로만 처리한다.
    expect(confidentFixes(segs.map((s) => s.label))).toEqual([
      { index: 1, from: "HH0307_0400080_ACV01", to: "HH0307_040_0080" },
    ]);
  });
  it("ignores a fix whose 'from' no longer matches that row", () => {
    // 회귀(실기): 씬별에서 미리보기를 연 뒤 시퀀스별로 바꾸면 옛 목록이 남는데,
    // 인덱스만 보고 적용하면 시퀀스 구간에 씬 라벨을 덮어쓴다. from이 현재
    // 라벨과 다르면(=다른 목록) 건너뛴다 — UI 초기화와 무관한 구조적 안전장치.
    const stale = [{ index: 0, from: "HH0307_040_0060", to: "HH0307_999_9999" }];
    const other = [{ label: "HH0307_010", start_ms: 0, end_ms: 9000 }];
    expect(applyFixes(other, stale, new Set([0]))[0]!.label).toBe("HH0307_010");
  });
  it("applies only the selected fixes and leaves the rest untouched", () => {
    const fixes = [
      { index: 1, from: "HH0307_0400080_ACV01", to: "HH0307_040_0080" },
      { index: 3, from: "HH0307_040_0110", to: "HH0307_040_0999" },
    ];
    const out = applyFixes(segs, fixes, new Set([1]));  // 1번만 체크
    expect(out[1]!.label).toBe("HH0307_040_0080");
    expect(out[3]!.label).toBe("HH0307_040_0110");     // 미체크 → 그대로
    expect(out[1]!.start_ms).toBe(1000);               // 시간은 안 건드린다
    expect(segs[1]!.label).toBe("HH0307_0400080_ACV01");  // 원본 불변
  });
});

describe("segmentThumbRange", () => {
  it("maps a segment's time span to thumbnail indices", () => {
    // interval 2000ms, 10 thumbs (0..9 → t=0,2,4,...18s)
    expect(segmentThumbRange(0, 6000, 2000, 10)).toEqual({ from: 0, to: 2 });
    expect(segmentThumbRange(88000, 90000, 2000, 100)).toEqual({ from: 44, to: 44 });
  });
  it("clamps to available thumbnails", () => {
    expect(segmentThumbRange(0, 999000, 2000, 5)).toEqual({ from: 0, to: 4 });
    expect(segmentThumbRange(999000, 999000, 2000, 5)).toEqual({ from: 4, to: 4 });
  });
  it("excludes the thumbnail before a refined (non-grid) boundary", () => {
    // 회귀(실기): 정밀화 후 경계가 2초 배수가 아니게 되면 floor(start)는 직전
    // 구간에 속한 썸네일을 포함해, 한 칸이 두 구간에 중복 하이라이트되고 클릭 시
    // 엉뚱한 프레임이 떴다. 썸네일 i(=시각 i*interval)는 start<=i*iv<end일 때만
    // 그 구간 소속이다.
    // 010: 4968~131968 → t=6000(3)부터, t=130000(65)까지
    expect(segmentThumbRange(4968, 131968, 2000, 743)).toEqual({ from: 3, to: 65 });
    // 020: 131968~251218 → t=132000(66)부터 (65는 아직 010)
    expect(segmentThumbRange(131968, 251218, 2000, 743)).toEqual({ from: 66, to: 125 });
  });
});

describe("segmentTailMs", () => {
  it("targets the last frame (1.5 frames before the exclusive end)", () => {
    // 24fps → frameMs≈41.67, 1.5f≈62.5 → end-62.5 반올림 = 4938.
    // 이 시각은 다음 씬 시작(=end)보다 한 프레임 이상 앞이라 꼬리 프레임을 집는다.
    expect(segmentTailMs(0, 5000, 24)).toBe(4938);
  });
  it("clamps to start for ultra-short scenes", () => {
    // 30ms(<1.5프레임) 구간은 당기면 시작보다 앞이 되므로 시작으로 클램프.
    expect(segmentTailMs(1000, 1030, 24)).toBe(1000);
  });
  it("defaults to NTSC fps when fps is unknown or zero", () => {
    expect(segmentTailMs(0, 5000, 0)).toBe(segmentTailMs(0, 5000, NTSC_FPS));
    expect(segmentTailMs(0, 5000)).toBe(segmentTailMs(0, 5000, NTSC_FPS));
  });
  it("lands on the exported clip's LAST frame, not one before it", () => {
    // 회귀(실기 HH0304_010_0070, 23.976fps): 클립 [28924, 36849) 은 익스포트가
    // f0=ceil(28924/frameMs)=694 부터 N=round((36849-28924)*fps/1000)=190장 →
    // 마지막 프레임 883(=클립 190번째). 이전 end-1.5f 어림은 882(=189번째)로 한
    // 프레임 일렀다. 꼬리 시각을 서버 -ss snap-up 하면 883이 나와야 한다.
    const frameMs = 1000 / NTSC_FPS;
    const tail = segmentTailMs(28924, 36849, NTSC_FPS);
    const snapUpFrame = Math.ceil(tail / frameMs - 1e-6); // 서버 썸네일이 집는 프레임
    const f0 = Math.ceil(28924 / frameMs - 1e-6);
    const n = Math.round((36849 - 28924) / frameMs);
    expect(snapUpFrame).toBe(f0 + n - 1); // = 883 = 익스포트 마지막 프레임
    expect(snapUpFrame - f0 + 1).toBe(190); // 클립의 190번째(=마지막) 프레임
  });
});

describe("frameSeekMs", () => {
  it("targets the middle of the frame the server -ss thumbnail selects", () => {
    // 실기 경계(HH0304 23.976fps): start_ms=28924는 프레임 693/694 간극중앙.
    // 서버 -ss snap-up은 프레임 694(=이 씬 첫 프레임)를 집는데, HTML5 video는
    // 28924를 '포함'하는 프레임 693(이전 씬)을 보여준다 → 팝업은 694 중앙으로.
    const seek = frameSeekMs(28924, NTSC_FPS);
    const frameMs = 1000 / NTSC_FPS;
    expect(Math.floor(seek / frameMs)).toBe(694); // HTML5가 표시할 프레임 = 694
  });
  it("uses source fps — 24fps vs NTSC differ by a frame at boundaries", () => {
    const frameMs24 = 1000 / 24;
    const frameMsN = 1000 / NTSC_FPS;
    // 같은 경계라도 fps가 다르면 인덱스가 갈린다(사용자 지적: 소스 fps를 따라야).
    expect(Math.floor(frameSeekMs(28924, 24) / frameMs24)).toBe(695);
    expect(Math.floor(frameSeekMs(28924, NTSC_FPS) / frameMsN)).toBe(694);
  });
  it("defaults to NTSC when fps unknown/zero", () => {
    expect(frameSeekMs(28924, 0)).toBe(frameSeekMs(28924, NTSC_FPS));
    expect(frameSeekMs(28924)).toBe(frameSeekMs(28924, NTSC_FPS));
  });
});

describe("shiftBoundaryMs", () => {
  const frameOf = (ms: number) => Math.ceil(ms / (1000 / NTSC_FPS) - 1e-6);
  it("moves a shared boundary by exactly N frames, frame-aligned", () => {
    const b = 28924; // 실기 _0070 시작 — export -ss가 집는 뒤 세그 첫 프레임 = 694
    expect(frameOf(b)).toBe(694);
    // +1: 경계가 뒤로 → 뒤 세그 첫 프레임 695(앞 세그가 프레임 하나 흡수)
    expect(frameOf(shiftBoundaryMs(b, NTSC_FPS, 1))).toBe(695);
    // -2: 경계가 앞으로 → 뒤 세그 첫 프레임 692(뒤 세그가 두 프레임 흡수)
    expect(frameOf(shiftBoundaryMs(b, NTSC_FPS, -2))).toBe(692);
  });
  it("clamps at zero and defaults fps when unknown", () => {
    expect(shiftBoundaryMs(10, NTSC_FPS, -100)).toBe(0);
    expect(shiftBoundaryMs(28924, 0, 0)).toBe(shiftBoundaryMs(28924, NTSC_FPS, 0));
  });
});

describe("frameNumberAt", () => {
  it("counts from 1 — the first frame is frame 1, not 0", () => {
    expect(frameNumberAt(0, 24)).toBe(1);
    expect(frameNumberAt(41, 24)).toBe(1);        // 0:00.041 < 1프레임(41.67ms) → 아직 1
    expect(frameNumberAt(1000 / 24, 24)).toBe(2); // 정확히 1프레임 경계 → 2프레임 시작
  });
  it("defaults to NTSC when fps unknown/zero", () => {
    expect(frameNumberAt(5000, 0)).toBe(frameNumberAt(5000, NTSC_FPS));
    expect(frameNumberAt(5000)).toBe(frameNumberAt(5000, NTSC_FPS));
  });
});

describe("segFrameNumber", () => {
  // 실기 _0070 클립(23.976fps): 익스포트 f0=694, N=190프레임.
  const START = 28924, END = 36849;
  it("head frame is 1, tail frame is N — matches exported clip frames", () => {
    const frameMs = 1000 / NTSC_FPS;
    const headMid = (694 + 0.5) * frameMs;   // frameSeekMs가 집는 첫 프레임 중앙
    const tailMid = (883 + 0.5) * frameMs;   // 마지막(190번째) 프레임 중앙
    expect(segFrameNumber(headMid, START, END, NTSC_FPS)).toEqual({ k: 1, n: 190 });
    expect(segFrameNumber(tailMid, START, END, NTSC_FPS)).toEqual({ k: 190, n: 190 });
  });
  it("clamps out-of-range times into [1, n]", () => {
    expect(segFrameNumber(0, START, END, NTSC_FPS).k).toBe(1);
    expect(segFrameNumber(END + 5000, START, END, NTSC_FPS).k).toBe(190);
  });
  it("defaults to NTSC when fps unknown/zero", () => {
    expect(segFrameNumber(30000, START, END, 0))
      .toEqual(segFrameNumber(30000, START, END, NTSC_FPS));
  });
});

describe("trimFrames", () => {
  // 팝업 카운터 '프레임 k / n'을 경계 이동 프레임 수로 바꾼다 — 사용자가 눈으로
  // 읽어 입력칸에 옮겨 적던 값을 In/Out 버튼이 그대로 계산한다.
  it("maps the current frame to give-away counts on both sides", () => {
    expect(trimFrames(31, 40)).toEqual({ inFrames: 30, outFrames: 9 });
  });
  it("gives away nothing at the matching end — 찍은 프레임은 이 씬에 남는다", () => {
    expect(trimFrames(1, 40)).toEqual({ inFrames: 0, outFrames: 39 });
    expect(trimFrames(40, 40)).toEqual({ inFrames: 39, outFrames: 0 });
  });
  it("can never empty the scene — 어느 쪽이든 최소 1프레임 남는다", () => {
    for (const k of [1, 2, 20, 39, 40]) {
      const { inFrames, outFrames } = trimFrames(k, 40);
      expect(40 - inFrames).toBeGreaterThanOrEqual(1);
      expect(40 - outFrames).toBeGreaterThanOrEqual(1);
    }
  });
  it("clamps out-of-range k like segFrameNumber does", () => {
    expect(trimFrames(0, 10)).toEqual({ inFrames: 0, outFrames: 9 });
    expect(trimFrames(99, 10)).toEqual({ inFrames: 9, outFrames: 0 });
    expect(trimFrames(1, 0)).toEqual({ inFrames: 0, outFrames: 0 });
  });
});

describe("matchesLabelQuery", () => {
  const L = "HH0304_010_0230";
  it("matches a partial slate number", () => {
    expect(matchesLabelQuery(L, "0230")).toBe(true);
    expect(matchesLabelQuery(L, "0231")).toBe(false);
  });
  it("ignores case, surrounding space, and delimiters", () => {
    expect(matchesLabelQuery(L, "hh0304")).toBe(true);
    expect(matchesLabelQuery(L, "  0230 ")).toBe(true);
    // 구분자를 빼고 비교해 "010_0230"·"010 0230"·"0100230"이 모두 같은 씬을 찾는다.
    expect(matchesLabelQuery(L, "010_0230")).toBe(true);
    expect(matchesLabelQuery(L, "0100230")).toBe(true);
  });
  it("treats an empty query as no filter", () => {
    expect(matchesLabelQuery(L, "")).toBe(true);
    expect(matchesLabelQuery(L, "   ")).toBe(true);
  });
});

describe("filterIndices", () => {
  const labels = ["HH_010_0010", "HH_010_0020", "HH_010_0230"];
  it("returns null (= 전체) when there is no tab filter and no query", () => {
    expect(filterIndices(labels, null, "")).toBeNull();
  });
  it("narrows to matching rows, keeping original indexes", () => {
    expect(filterIndices(labels, null, "0230")).toEqual([2]);
    expect(filterIndices(labels, null, "hh_010")).toEqual([0, 1, 2]);
  });
  it("intersects with the tab filter — 오독/경계 탭 안에서만 검색된다", () => {
    expect(filterIndices(labels, [1, 2], "")).toEqual([1, 2]);
    expect(filterIndices(labels, [1, 2], "0010")).toEqual([]);
    expect(filterIndices(labels, [1, 2], "0020")).toEqual([1]);
  });
});

describe("stepVisibleIndex", () => {
  const visible = [2, 5, 9];
  it("moves within the visible list", () => {
    expect(stepVisibleIndex(visible, 5, 1)).toBe(9);
    expect(stepVisibleIndex(visible, 5, -1)).toBe(2);
  });
  it("stops at the ends instead of wrapping", () => {
    expect(stepVisibleIndex(visible, 9, 1)).toBeNull();
    expect(stepVisibleIndex(visible, 2, -1)).toBeNull();
  });
  it("starts from the near end when nothing is selected", () => {
    expect(stepVisibleIndex(visible, null, 1)).toBe(2);
    expect(stepVisibleIndex(visible, null, -1)).toBe(9);
  });
  it("jumps to the nearest visible row when the selection was filtered out", () => {
    expect(stepVisibleIndex(visible, 6, 1)).toBe(9);
    expect(stepVisibleIndex(visible, 6, -1)).toBe(5);
    expect(stepVisibleIndex(visible, 99, 1)).toBeNull();
  });
  it("returns null for an empty list", () => {
    expect(stepVisibleIndex([], null, 1)).toBeNull();
  });
});

describe("neighborIndices", () => {
  // 개별 씬 익스포트는 맞닿은 이웃까지 다시 굽는다 — 경계를 옮기면 이웃의 프레임
  // 수도 함께 바뀌어, 이 씬만 내보내면 이웃 파일이 옛 경계로 남는다.
  it("includes both neighbors in ascending order", () => {
    expect(neighborIndices(5, 10)).toEqual([4, 5, 6]);
  });
  it("clamps at the list ends", () => {
    expect(neighborIndices(0, 10)).toEqual([0, 1]);
    expect(neighborIndices(9, 10)).toEqual([8, 9]);
    expect(neighborIndices(0, 1)).toEqual([0]);
  });
  it("returns nothing for an index outside the list", () => {
    expect(neighborIndices(-1, 5)).toEqual([]);
    expect(neighborIndices(5, 5)).toEqual([]);
    expect(neighborIndices(0, 0)).toEqual([]);
  });
});

describe("scenePopupAction", () => {
  it("maps the popup review keys", () => {
    expect(scenePopupAction({ code: "KeyI", key: "i" })).toBe("trimIn");
    expect(scenePopupAction({ code: "KeyO", key: "o" })).toBe("trimOut");
    expect(scenePopupAction({ code: "KeyG", key: "g" })).toBe("prevScene");
    expect(scenePopupAction({ code: "KeyH", key: "h" })).toBe("nextScene");
    expect(scenePopupAction({ code: "BracketLeft", key: "[" })).toBe("toHead");
    expect(scenePopupAction({ code: "BracketRight", key: "]" })).toBe("toTail");
  });

  // 한글 입력 상태에서 G/H는 e.key가 "ㅎ"/"ㅗ"(또는 "Process")로 온다 —
  // 물리 키로 받지 않으면 단축키가 조용히 안 먹는다(이 앱의 기본 입력 상태).
  it("works while the Korean IME is on", () => {
    expect(scenePopupAction({ code: "KeyG", key: "ㅎ" })).toBe("prevScene");
    expect(scenePopupAction({ code: "KeyH", key: "ㅗ" })).toBe("nextScene");
    expect(scenePopupAction({ code: "KeyI", key: "Process" })).toBe("trimIn");
  });

  it("maps S to split, by code and by key (한글 입력 상태 포함)", () => {
    // 한글 IME에서는 key가 'ㄴ'으로 오므로 code를 함께 본다(기존 키들과 동일 정책).
    expect(scenePopupAction({ code: "KeyS", key: "ㄴ" })).toBe("split");
    expect(scenePopupAction({ key: "s" })).toBe("split");
    expect(scenePopupAction({ key: "S" })).toBe("split");
  });

  it("accepts the shifted brackets on the same physical keys", () => {
    expect(scenePopupAction({ code: "BracketLeft", key: "{" })).toBe("toHead");
    expect(scenePopupAction({ code: "BracketRight", key: "}" })).toBe("toTail");
    expect(scenePopupAction({ key: "{" })).toBe("toHead");
  });

  it("falls back to the character when code is missing", () => {
    expect(scenePopupAction({ key: "G" })).toBe("prevScene");
    expect(scenePopupAction({ key: "h" })).toBe("nextScene");
  });

  it("ignores keys with no mapping", () => {
    expect(scenePopupAction({ code: "KeyJ", key: "j" })).toBeNull();
    expect(scenePopupAction({ code: "ArrowLeft", key: "ArrowLeft" })).toBeNull();
    expect(scenePopupAction({})).toBeNull();
  });
});

describe("scanProgressKey", () => {
  // 실기: 판독 카운터가 N/N에 닿은 뒤 재시도 단계가 몇 분 돌자, 프론트가
  // 200초 무변화를 정체로 보고 멀쩡한 스캔에 "진행되지 않습니다"를 띄웠다.
  // 진척 판정은 판독 수 '와' 앞 구간의 살아있음 신호를 함께 봐야 한다.
  it("changes when the OCR counter advances", () => {
    expect(scanProgressKey({ ocr_done: 10 }))
      .not.toBe(scanProgressKey({ ocr_done: 11 }));
  });

  it("changes when only the extraction tick advances", () => {
    expect(scanProgressKey({ ocr_done: 0, stage_tick: 120 }))
      .not.toBe(scanProgressKey({ ocr_done: 0, stage_tick: 340 }));
  });

  it("stays put when nothing moved (real stall)", () => {
    expect(scanProgressKey({ ocr_done: 2791, stage_tick: 5 }))
      .toBe(scanProgressKey({ ocr_done: 2791, stage_tick: 5 }));
  });

  it("treats missing fields as zero", () => {
    expect(scanProgressKey({})).toBe(scanProgressKey({ ocr_done: 0, stage_tick: 0 }));
  });
});

describe("mergeNeighborHint", () => {
  // 필터(경계 오류·오독 탭)를 걸면 병합 대상인 '진짜 이웃'이 목록에서 사라져,
  // 어느 쪽으로 병합할지 판단할 근거가 없었다(실기 Seene656).
  const ULD = "U1L4D";     // 이 쇼의 정상 라벨 모양: Scene + 숫자(자릿수 무관)
  const P = "Scene";       // 이 쇼의 공통 접두 — 정상/깨짐 판정에 함께 쓴다

  it("says either side when both neighbours are the same scene", () => {
    expect(mergeNeighborHint({ label: "Sgene663", prev: "Scene663",
      next: "Scene663", validClass: ULD, validPrefix: P })).toBe("both");
  });

  it("prefers the neighbour the misread label almost matches", () => {
    expect(mergeNeighborHint({ label: "Seene656", prev: "Scene655",
      next: "Scene656", validClass: ULD, validPrefix: P })).toBe("next");
    expect(mergeNeighborHint({ label: "Scene~665", prev: "Scene665",
      next: "Scene666", validClass: ULD, validPrefix: P })).toBe("prev");
  });

  it("trusts an exact suggestion match over raw distance", () => {
    // 실기 '678': 접두가 통째로 안 읽혔다. 제안(Scene678)이 뒤 이웃과 정확히
    // 일치하므로 글자수(앞뒤 모두 5)가 동점이어도 뒤쪽으로 확정한다.
    expect(mergeNeighborHint({ label: "678", prev: "Scene677", next: "Scene678",
      suggestion: "Scene678", validClass: ULD, validPrefix: P })).toBe("next");
  });

  // ── 회귀: 멀쩡한 씬을 깨진 조각에 병합하라고 추천하던 문제 ──────────────
  // 병합은 언제나 '이웃의 이름'이 살아남는다. 깨진 이웃 쪽으로 추천하면 멀쩡한
  // 이름이 사라진다(실기: Scene678에 '◀ 678'이 추천으로 떴다).
  it("never points at a malformed neighbour", () => {
    expect(mergeNeighborHint({ label: "Scene678", prev: "678", next: null,
      validClass: ULD, validPrefix: P })).toBeNull();
  });

  it("stays silent when only one side is comparable", () => {
    // 실기 'Scene'(45 앞): 뒤 이웃이 깨져 비교가 안 된다 — 남은 한쪽으로
    // 떠밀면 틀린다(정답은 뒤쪽 '45'가 Scene45로 고쳐진 뒤다).
    expect(mergeNeighborHint({ label: "Scene", prev: "Scene44", next: "45",
      validClass: ULD, validPrefix: P })).toBeNull();
  });

  it("still resolves one-sided cases when the repair matches exactly", () => {
    expect(mergeNeighborHint({ label: "678", prev: "678x", next: "Scene678",
      suggestion: "Scene678", validClass: ULD, validPrefix: P })).toBe("next");
  });

  it("stays silent when neither neighbour is close (no forced hint)", () => {
    expect(mergeNeighborHint({ label: "Scene34NPanel2ZN", prev: "Scene34",
      next: "Scene35", validClass: ULD, validPrefix: P })).toBeNull();
  });

  it("stays silent on a tie", () => {
    expect(mergeNeighborHint({ label: "20206", prev: "Scene658",
      next: "Scene659", validClass: ULD, validPrefix: P })).toBeNull();
  });

  it("has nothing to say at the list ends without evidence", () => {
    expect(mergeNeighborHint({ label: "678", prev: null, next: "Scene678",
      validClass: ULD, validPrefix: P })).toBeNull();
    expect(mergeNeighborHint({ label: "678", prev: null, next: null,
      validClass: ULD, validPrefix: P })).toBeNull();
  });

  // ── 회귀: 정상 씬마다 추천이 뜨던 문제 ─────────────────────────────────
  // 씬 번호는 이웃과 한두 글자 차이라(Scene19 vs Scene18) 거리만 보면 목록이
  // 온통 초록이 된다. 실기 321씬에서 멀쩡한 씬 수십 개에 추천이 떴다.
  it("says nothing about a well-formed scene", () => {
    expect(mergeNeighborHint({ label: "Scene19", prev: "Scene18",
      next: "Scene20", validClass: ULD, validPrefix: P })).toBeNull();
    expect(mergeNeighborHint({ label: "Scene7", prev: "Scene6",
      next: "Scene8", validClass: ULD, validPrefix: P })).toBeNull();
  });

  it("still catches a typo that keeps the normal shape", () => {
    // 'Seene9'는 글자+숫자라 모양만으로는 정상과 구분되지 않는다 — 접두가 근거.
    expect(mergeNeighborHint({ label: "Seene9", prev: "Scene9", next: "Scene10",
      validClass: ULD, validPrefix: P })).toBe("prev");
  });
});

describe("labelClassKey / modalLabelClass / modalLabelPrefix", () => {
  it("collapses digit-run lengths so Scene7 and Scene678 are the same shape", () => {
    expect(labelClassKey("Scene7")).toBe(labelClassKey("Scene678"));
    expect(labelClassKey("678")).not.toBe(labelClassKey("Scene678"));
    expect(labelClassKey("Scene~665")).not.toBe(labelClassKey("Scene665"));
  });

  it("finds the shape most labels share", () => {
    expect(modalLabelClass(["Scene1", "Scene22", "Scene678", "678", "SeRe"]))
      .toBe("U1L4D");
    expect(modalLabelClass([])).toBeNull();
  });

  it("finds the prefix most labels start with", () => {
    expect(modalLabelPrefix(["Scene1", "Scene22", "Scene678", "678"], "U1L4D"))
      .toBe("Scene");
  });

  it("is not destroyed by a stray misread (최장 공통 접두였다면 빈 문자열)", () => {
    // 실기 321씬: 'Bene603' 한 건 때문에 최장 공통 접두가 통째로 날아갔다.
    const corpus = ["Scene1", "Scene2", "Scene3", "Scene4", "Scene5", "Scene6",
                    "Scene7", "Scene8", "Scene9", "Sdene10", "Bene603"];
    expect(modalLabelPrefix(corpus, "U1L4D")).toBe("Scene");
  });

  it("refuses to call a prefix a rule when the show uses several", () => {
    // AA/BB가 섞인 작품에서 다수 접두를 규칙으로 삼으면 소수 쪽이 통째로
    // 오독 취급된다 — 지배적이지 않으면 접두 판정을 쓰지 않는다.
    expect(modalLabelPrefix(
      ["AAscene1", "AAscene2", "AAscene3", "BBscene4", "BBscene5"], "U2L5D"))
      .toBe("");
  });

  it("judges well-formedness by shape AND prefix", () => {
    const wf = (l: string) => isWellFormedLabel(l, "U1L4D", "Scene");
    expect(wf("Scene19")).toBe(true);
    expect(wf("Scene7")).toBe(true);
    expect(wf("Seene9")).toBe(false);    // 모양은 같지만 접두가 다르다
    expect(wf("Sdene94")).toBe(false);
    expect(wf("678")).toBe(false);
    expect(wf("Scene,63")).toBe(false);  // 접두는 맞지만 모양이 다르다
  });
});

describe("anomalousLabels — 접두 복원 제안", () => {
  // 이 쇼의 슬레이트는 전부 "Scene"으로 시작한다. OCR이 접두를 통째로/부분으로
  // 흘린 조각은 접두를 되살리면 정상 라벨이 된다(실기 '678' → 'Scene678').
  const corpus = [
    "Scene676", "Scene677", "678", "Scene678", "Scene8", "ene8", "Scene9",
    "Scene14", "15", "Scene15", "Scene352", "58", "Scene353",
  ];
  const find = (label: string) =>
    anomalousLabels(corpus).find((a) => a.label === label);

  it("restores a fully dropped prefix", () => {
    expect(find("678")?.suggestion).toBe("Scene678");
  });

  it("splices a partially dropped prefix instead of doubling it", () => {
    // 'ene8'에 접두를 그냥 붙이면 'Sceneene8'이 된다 — 겹치는 만큼 물려 붙인다.
    expect(find("ene8")?.suggestion).toBe("Scene8");
  });

  it("is confident only when a neighbour confirms the number", () => {
    expect(find("678")?.confident).toBe(true);     // 뒤 이웃이 Scene678
    expect(find("15")?.confident).toBe(true);      // 뒤 이웃이 Scene15
    // '58'은 Scene352와 Scene353 사이 — Scene58은 문맥상 근거가 없다.
    expect(find("58")?.confident).toBe(false);
  });

  it("leaves labels the prefix cannot repair alone", () => {
    expect(anomalousLabels([...corpus, "A"]).find((a) => a.label === "A")
      ?.suggestion).toBeNull();   // SceneA는 정상 모양이 아니다
  });
});

describe("접두 복원 — 깨진 글자 머리를 접두로 되돌린다", () => {
  // 이 쇼의 슬레이트는 전부 "Scene"으로 시작한다. 머리가 접두와 한두 글자
  // 차이면 접두 오독으로 보고 되돌린다. 예전엔 접두를 '덧칠'해 'Scenecane60'
  // 같은 제안이 나왔다(실기).
  const corpus = ["Scene59", "cane60", "Scene60", "Scene93", "scene94",
                  "Scene94", "Scene95", "677", "Scene677", "Scéne639",
                  "BOBBYp9", "20206", "Scene638", "Scene640"];
  const find = (label: string) =>
    anomalousLabels(corpus).find((a) => a.label === label);

  it("repairs a broken prefix instead of stacking on top of it", () => {
    expect(find("cane60")?.suggestion).toBe("Scene60");
    expect(find("scene94")?.suggestion).toBe("Scene94");
    expect(find("Scéne639")?.suggestion).toBe("Scene639");
  });

  it("restores a cleanly dropped prefix", () => {
    expect(find("677")?.suggestion).toBe("Scene677");
    expect(find("677")?.confident).toBe(true);   // 뒤 이웃이 Scene677
  });

  it("leaves text that is not a broken prefix alone", () => {
    // 'BOBBYp'는 접두 오독이 아니라 딴 텍스트다 — 손대지 않는다.
    expect(find("BOBBYp9")?.suggestion).toBeNull();
  });

  it("refuses a number the show never uses", () => {
    // 관측된 번호는 세 자리까지 — 'Scene20206'은 헛제안이다(실기).
    expect(find("20206")?.suggestion).toBeNull();
  });

  it("stays quiet about merging when the repair is its own scene", () => {
    // 'Scéne639'의 이웃은 638·640 — 639는 이 줄에만 있다. 병합을 권하면 씬이
    // 사라진다. 이름만 고치면 되는 경우다.
    expect(mergeNeighborHint({ label: "Scéne639", prev: "Scene638",
      next: "Scene640", suggestion: "Scene639",
      validClass: "U1L4D", validPrefix: "Scene" })).toBeNull();
  });
});


describe("labelClassKey — 글자 런 길이는 남긴다", () => {
  it("keeps Scene7 and Scene678 the same but flags an extra letter", () => {
    expect(labelClassKey("Scene7")).toBe(labelClassKey("Scene678"));
    // 'Scenel311'은 글자가 하나 더 낀 오독 — 정상과 구분돼야 이웃 자격에서 빠진다.
    expect(labelClassKey("Scenel311")).not.toBe(labelClassKey("Scene311"));
  });

  it("keeps a malformed neighbour out of the hint", () => {
    expect(mergeNeighborHint({ label: "Scene31p", prev: "Scenel309",
      next: "Scenel311", validClass: "U1L4D", validPrefix: "Scene" })).toBeNull();
  });
});

describe("anomalousLabels — 번호 자릿수가 늘어나는 쇼", () => {
  // 실기 321씬: Scene1 … Scene678로 번호가 1~3자리라, 자릿수를 고정으로 보는
  // 템플릿이 멀쩡한 씬 123개를 오독으로 몰았다(오독 목록 180행).
  const varied = ["Scene1", "Scene2", "Scene9", "Scene10", "Scene11",
                  "Scene100", "Scene101", "Scene678", "678", "Seene9"];

  it("does not flag a shorter number as a misread", () => {
    const flagged = anomalousLabels(varied).map((a) => a.label);
    expect(flagged).not.toContain("Scene1");
    expect(flagged).not.toContain("Scene678");
  });

  it("still flags a dropped prefix and a broken one", () => {
    const flagged = anomalousLabels(varied).map((a) => a.label);
    expect(flagged).toContain("678");
    expect(flagged).toContain("Seene9");   // 모양은 같지만 접두가 다르다
  });

  it("keeps the strict check when the show pads its numbers", () => {
    // 자릿수가 고정된 쇼에서는 한 자리 빠진 것이 진짜 오독이다 — 관용 금지.
    const padded = ["HH_010_0010", "HH_010_0020", "HH_010_0030",
                    "HH_010_0040", "HH_010_050"];
    expect(anomalousLabels(padded).map((a) => a.label))
      .toContain("HH_010_050");
  });
});

describe("exportedFileName", () => {
  // 서버가 윈도우면 역슬래시 경로가 온다 — 클라가 맥이어도 파일명을 뽑아야 한다.
  it("takes the name from either separator", () => {
    expect(exportedFileName("D:\\out\\Scene678.mp4")).toBe("Scene678.mp4");
    expect(exportedFileName("/srv/out/Scene678.mp4")).toBe("Scene678.mp4");
    expect(exportedFileName("Scene678.mp4")).toBe("Scene678.mp4");
  });
});

describe("splitSegment", () => {
  const fps = 24;
  const segs = [
    { label: "A", start_ms: 0, end_ms: 10000 },
    { label: "B", start_ms: 10000, end_ms: 20000 },
  ];

  it("cuts where the In trim would put the boundary", () => {
    // 분할과 트림이 다른 수식을 쓰면 익스포트 -ss snap-up과 어긋나 프레임이 밀린다.
    const out = splitSegment(segs, 1, 5, fps);
    const cut = shiftBoundaryMs(10000, fps, 4);
    expect(out).toHaveLength(3);
    expect(out[1]).toEqual({ label: "B_cut", start_ms: 10000, end_ms: cut });
    expect(out[2]).toEqual({ label: "B", start_ms: cut, end_ms: 20000 });
  });

  it("marks the leading part with _cut so the two rows never share a name", () => {
    // 같은 이름 두 줄이면 어느 쪽을 고쳐야 할지 알 수 없고, 익스포트 파일명에도
    // dedupe 접미사가 붙어 헷갈린다. 뒤 구간이 원래(읽어낸) 이름을 유지한다.
    const out = splitSegment(segs, 1, 5, fps);
    expect(out[1]!.label).toBe("B_cut");
    expect(out[2]!.label).toBe("B");
  });

  it("keeps the placeholder unique when the same scene is split again", () => {
    const once = splitSegment(segs, 1, 5, fps);
    const twice = splitSegment(once, 2, 5, fps);
    const labels = twice.map((s) => s.label);
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels).toContain("B_cut2");
  });

  it("does not stack _cut when the leading _cut part is split again", () => {
    // 자리표시자 줄을 이어서 나누면 base가 "B_cut"이라 그대로 붙이면 "B_cut_cut"이
    // 된다(실기 2026-07-28) — 접미사를 벗기고 번호를 올려 "B_cut2"가 돼야 한다.
    const once = splitSegment(segs, 1, 5, fps);   // [A, B_cut, B]
    const twice = splitSegment(once, 1, 3, fps);  // 앞 조각(B_cut)을 또 나눈다
    const labels = twice.map((s) => s.label);
    expect(new Set(labels).size).toBe(labels.length);
    expect(labels).toContain("B_cut2");
    expect(labels.some((l) => l.includes("_cut_cut"))).toBe(false);
  });

  it("leaves the timeline continuous — no gap, no overlap, same total span", () => {
    const out = splitSegment(segs, 1, 5, fps);
    expect(out[0]!.end_ms).toBe(out[1]!.start_ms);
    expect(out[1]!.end_ms).toBe(out[2]!.start_ms);
    expect(out[0]!.start_ms).toBe(0);
    expect(out.at(-1)!.end_ms).toBe(20000);
  });

  it("refuses the first frame — a 0-frame part exports a 0-byte clip", () => {
    expect(splitSegment(segs, 1, 1, fps)).toBe(segs);
  });

  it("refuses an out-of-range index or a frame past the end", () => {
    expect(splitSegment(segs, 5, 3, fps)).toBe(segs);
    expect(splitSegment(segs, 1, 100000, fps)).toBe(segs);
  });
});

describe("applySplitName", () => {
  const segs = [
    { label: "A", start_ms: 0, end_ms: 1000 },
    { label: "B_cut", start_ms: 1000, end_ms: 1500 },
    { label: "B", start_ms: 1500, end_ms: 2000 },
  ];

  it("names the placeholder row that is still sitting there", () => {
    const out = applySplitName(segs, 1, "B_cut", "B_0280");
    expect(out.map((s) => s.label)).toEqual(["A", "B_0280", "B"]);
  });

  it("does nothing when that row is no longer the placeholder", () => {
    // 슬레이트를 읽는 동안 사용자가 되돌리거나 다른 편집을 했을 수 있다 — 그때
    // 이름을 얹으면 엉뚱한 줄을 덮어쓴다.
    expect(applySplitName(segs, 2, "B_cut", "B_0280")).toBe(segs);
    expect(applySplitName(segs, 9, "B_cut", "B_0280")).toBe(segs);
  });

  it("refuses a name that would duplicate another row", () => {
    // _cut 줄을 이어 나눈 앞 조각에는 OCR이 대개 '이미 목록에 있는 진짜 이름'을
    // 제안한다(뒤쪽 원래 줄과 중복). 얹으면 같은 이름 두 줄 — _cut을 만든 이유가
    // 통째로 무효가 되므로 자리표시자를 남긴다(실기 2026-07-28: _cut이 사라지고
    // Seg01A_S11 두 줄이 남았다).
    const stacked = [
      { label: "B_cut2", start_ms: 1000, end_ms: 1200 },
      { label: "B_cut", start_ms: 1200, end_ms: 1500 },
      { label: "B", start_ms: 1500, end_ms: 2000 },
    ];
    expect(applySplitName(stacked, 0, "B_cut2", "B")).toBe(stacked);
  });
});

describe("boundaryIssueIndices", () => {
  const segs = [
    { label: "A", start_ms: 0, end_ms: 1000 },
    { label: "B", start_ms: 1000, end_ms: 2000 },
  ];

  it("resolves each issue by current label, not a stored index", () => {
    // 병합·분할로 목록 길이가 바뀌어도 엉뚱한 줄을 가리키면 안 된다.
    expect(boundaryIssueIndices([{ label: "B" }], segs, [])).toEqual([1]);
    expect(boundaryIssueIndices([{ label: "gone" }], segs, [])).toEqual([]);
  });

  it("hides a scene the user confirmed is fine", () => {
    const ok = [{ label: "B", start_ms: 1000, end_ms: 2000 }];
    expect(boundaryIssueIndices([{ label: "B" }], segs, ok)).toEqual([]);
  });

  it("brings it back once that boundary moves — the new cut was never reviewed", () => {
    const ok = [{ label: "B", start_ms: 1000, end_ms: 2000 }];
    const moved = [segs[0]!, { label: "B", start_ms: 1200, end_ms: 2000 }];
    expect(boundaryIssueIndices([{ label: "B" }], moved, ok)).toEqual([1]);
  });
});

describe("probeToken", () => {
  it("encodes bytes as lowercase hex, two digits each", () => {
    expect(probeToken(new Uint8Array([0, 15, 171, 255]))).toBe("000fabff");
  });

  it("stays inside the shape the server validates", () => {
    // 서버가 ^[0-9a-f]+$ · 8~64자로 검증한다(BoundaryOk와 달리 여긴 pattern이 있다).
    // 모양이 어긋나면 422가 나고 탐침은 조용히 실패해 느린 중계 경로로 떨어진다 —
    // 에러가 안 뜨므로 눈치채기 어렵다. 그래서 모양을 여기서 잠근다.
    const token = probeToken(new Uint8Array(8));
    expect(token).toMatch(/^[0-9a-f]{16}$/);
  });
});

describe("upsertBoundaryOk", () => {
  const a = { label: "A", start_ms: 0, end_ms: 100 };
  const b = { label: "B", start_ms: 100, end_ms: 200 };

  it("replaces the entry for the same label instead of piling up", () => {
    // 같은 씬을 두 번 확인하면 항목이 쌓여 어느 경계가 기준인지 알 수 없게 된다.
    const moved = { label: "B", start_ms: 120, end_ms: 200 };
    expect(upsertBoundaryOk([a, b], moved)).toEqual([a, moved]);
  });

  it("appends a label that was not confirmed yet", () => {
    expect(upsertBoundaryOk([a], b)).toEqual([a, b]);
  });
});

describe("probeFileName", () => {
  it("keeps the yeson_probe_ prefix shared with Rust and the server", () => {
    // 이 접두사는 3개 언어가 함께 지키는 계약이다: Rust probe_file_write/remove가
    // 이걸로 시작하지 않는 경로를 거부하고, 서버도 같은 이름으로 파일을 찾는다.
    // 한쪽만 바뀌면 탐침이 조용히 실패해 같은 PC에서도 느린 중계 경로로 떨어진다.
    expect(probeFileName("aaaabbbbccccdddd"))
      .toBe("yeson_probe_aaaabbbbccccdddd.tmp");
    expect(probeFileName("ddddccccbbbbaaaa").startsWith("yeson_probe_")).toBe(true);
  });
});
