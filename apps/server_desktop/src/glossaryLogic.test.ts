import { describe, expect, it } from "vitest";

import { invalidLines, parseEntryLine, renderBlocks, resolveGlossaryFile } from "./glossaryLogic";

describe("parseEntryLine", () => {
  it("=> 구분자를 최우선으로 해석한다", () => {
    expect(parseEntryLine("Toon Boom => 툰붐")).toEqual({ en: "Toon Boom", ko: "툰붐" });
  });

  it("탭과 = 구분자도 지원한다(서버 파서 미러)", () => {
    expect(parseEntryLine("mesh\t메시")).toEqual({ en: "mesh", ko: "메시" });
    expect(parseEntryLine("rig = 릭")).toEqual({ en: "rig", ko: "릭" });
  });

  it("한쪽이 비면 무효", () => {
    expect(parseEntryLine("Maya =>")).toBeNull();
    expect(parseEntryLine("=> 마야")).toBeNull();
  });

  it("주석·빈 줄은 항목이 아니다", () => {
    expect(parseEntryLine("# Toon Boom => 툰붐")).toBeNull();
    expect(parseEntryLine("  ")).toBeNull();
  });
});

describe("invalidLines", () => {
  it("파싱 불가 줄의 번호를 보고한다(주석·빈 줄 제외)", () => {
    const content = "Toon Boom => 툰붐\n오타줄\n\n# 주석\nMaya =>\n";
    expect(invalidLines(content).map((b) => b.line)).toEqual([2, 5]);
  });
});

describe("renderBlocks", () => {
  it("─ 장식 주석은 제목, 일반 주석은 설명 문단으로", () => {
    const blocks = renderBlocks("# ── 3D 툴 ──\n# 표기를 바꾸려면 오른쪽만 수정\n");
    expect(blocks[0]).toEqual({ kind: "heading", text: "3D 툴" });
    expect(blocks[1]).toEqual({ kind: "comment", text: "표기를 바꾸려면 오른쪽만 수정" });
  });

  it("연속 항목은 표 하나로 묶고, 빈 줄이 표를 끊는다", () => {
    const blocks = renderBlocks("a => 에이\nb => 비\n\nc => 씨\n");
    expect(blocks).toEqual([
      { kind: "entries", rows: [{ en: "a", ko: "에이" }, { en: "b", ko: "비" }] },
      { kind: "entries", rows: [{ en: "c", ko: "씨" }] },
    ]);
  });

  it("파싱 불가 줄은 줄 번호와 함께 오류 블록으로", () => {
    const blocks = renderBlocks("a => 에이\n오타줄\n");
    expect(blocks[1]).toEqual({ kind: "invalid", line: 2, text: "오타줄" });
  });
});

describe("resolveGlossaryFile", () => {
  const data = { glossary: { content: "a => 에이", terms: 1, effective_terms: 1 } };

  it("응답에 키가 있으면 그대로 지원됨으로 반환한다", () => {
    expect(resolveGlossaryFile(data, "glossary")).toEqual({
      supported: true,
      content: "a => 에이",
      terms: 1,
      effective_terms: 1,
    });
  });

  it("응답에 키가 없으면(구버전 서버) 빈 내용 + 미지원으로 반환한다", () => {
    expect(resolveGlossaryFile(data, "glossary_dialogue")).toEqual({
      supported: false,
      content: "",
      terms: 0,
      effective_terms: 0,
    });
  });

  it("data 자체가 null/undefined거나 값의 모양이 다르면 안전하게 미지원 처리한다", () => {
    expect(resolveGlossaryFile(null, "glossary").supported).toBe(false);
    expect(resolveGlossaryFile(undefined, "glossary").supported).toBe(false);
    expect(
      resolveGlossaryFile({ glossary: { content: "a" } }, "glossary").supported,
    ).toBe(false);
  });
});
