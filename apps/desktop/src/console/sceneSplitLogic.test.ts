import { describe, expect, it } from "vitest";
import { formatMs, previewLabel, tokenizeSlate } from "./sceneSplitLogic";

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
