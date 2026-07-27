import { describe, expect, it } from "vitest";
import { absorbFlankedMisreads, anomalousLabels, applyFixes, confidentFixes, formatMs, frameNumberAt, frameSeekMs, mergeAdjacentSameLabel, regionFromDrag, labelTemplate, mergeSegment, NTSC_FPS, previewLabel, renameSegment, segFrameNumber, segmentTailMs, segmentThumbRange, shiftBoundaryMs, suggestLabelFix, tokenShape, tokenizeSlate, trimFrames, neighborIndices, matchesLabelQuery, filterIndices, stepVisibleIndex } from "./sceneSplitLogic";

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
