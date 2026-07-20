import { describe, expect, it } from "vitest";
import { anomalousLabels, applyFixes, confidentFixes, formatMs, labelTemplate, mergeSegment, previewLabel, renameSegment, segmentThumbRange, suggestLabelFix, tokenShape, tokenizeSlate } from "./sceneSplitLogic";

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
  it("applies only the selected fixes and leaves the rest untouched", () => {
    const fixes = [
      { index: 1, from: "x", to: "HH0307_040_0080" },
      { index: 3, from: "y", to: "HH0307_040_0999" },
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
