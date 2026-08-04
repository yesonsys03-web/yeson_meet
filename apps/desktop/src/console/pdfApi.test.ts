import { describe, expect, it, vi } from "vitest";

// apiBase() → loadValues() throws on the real default ("" serverWsBase) unless
// a valid ws:// value is mocked in — same pattern as videoApi.test.ts.
vi.mock("../setup/setupValues", async (importOriginal) => {
  const real = await importOriginal<typeof import("../setup/setupValues")>();
  return {
    ...real,
    loadValues: () => ({ ...real.DEFAULT_VALUES, serverWsBase: "ws://localhost:8000" }),
  };
});

import { collectAllLabels, isActivePdfStatus, pdfPageUrl,
  type PdfLabelItem, type PdfLabelsResponse } from "./pdfApi";

function stubItem(i: number): PdfLabelItem {
  return {
    id: `i${i}`, origin: "auto", kind: "panel_label", page: Math.floor(i / 2),
    panel_index: null, rect: [0, 0, 1, 1], fontsize: 12,
    source_text: `S${i}`, text: `T${i}`, edited: false, editable: true,
  };
}

/** 서버 응답 흉내 — `total`개를 `pageSize`씩 잘라 준다. */
function fakeServer(total: number, pageSize: number, version = 7) {
  const all = Array.from({ length: total }, (_, i) => stubItem(i));
  const calls: number[] = [];
  const fetchPage = async (offset: number): Promise<PdfLabelsResponse> => {
    calls.push(offset);
    return {
      items: all.slice(offset, offset + pageSize), total,
      edits_version: version, stale: false, dangling: [], unresolved: [],
    };
  };
  return { fetchPage, calls };
}

describe("collectAllLabels", () => {
  it("500 상한을 넘는 문서도 전량을 잇는다", async () => {
    // 실물 표본(1037p = 1321개)에서 한 번만 부르면 821개가 사라졌다.
    const { fetchPage, calls } = fakeServer(1321, 500);
    const out = await collectAllLabels(fetchPage);
    expect(out.items).toHaveLength(1321);
    expect(out.items[1320]?.id).toBe("i1320");
    expect(calls).toEqual([0, 500, 1000]);
  });

  it("한 번에 다 들어오면 더 부르지 않는다", async () => {
    const { fetchPage, calls } = fakeServer(12, 500);
    expect((await collectAllLabels(fetchPage)).items).toHaveLength(12);
    expect(calls).toEqual([0]);
  });

  it("메타데이터는 첫 응답 것을 쓴다", async () => {
    const { fetchPage } = fakeServer(600, 500, 9);
    const out = await collectAllLabels(fetchPage);
    expect(out.edits_version).toBe(9);
    expect(out.total).toBe(600);
  });

  it("받는 중 편집이 끼어들면 이어붙이지 않고 다시 받는다", async () => {
    let version = 1;
    const calls: number[] = [];
    const fetchPage = async (offset: number): Promise<PdfLabelsResponse> => {
      calls.push(offset);
      // 첫 시도의 2쪽에서만 버전이 어긋난다.
      const v = calls.length === 2 ? 2 : version;
      if (calls.length === 2) version = 2;
      return {
        items: Array.from({ length: Math.min(500, 600 - offset) },
          (_, i) => stubItem(offset + i)),
        total: 600, edits_version: v, stale: false, dangling: [], unresolved: [],
      };
    };
    const out = await collectAllLabels(fetchPage);
    expect(out.items).toHaveLength(600);
    expect(out.edits_version).toBe(2);
    expect(calls).toEqual([0, 500, 0, 500]);  // 처음부터 다시
  });

  it("계속 어긋나면 조용히 어긋난 목록 대신 오류를 낸다", async () => {
    let version = 0;
    const fetchPage = async (offset: number): Promise<PdfLabelsResponse> => {
      version += 1;
      return {
        items: Array.from({ length: 500 }, (_, i) => stubItem(offset + i)),
        total: 600, edits_version: version, stale: false,
        dangling: [], unresolved: [],
      };
    };
    await expect(collectAllLabels(fetchPage)).rejects.toThrow(/다시 시도/);
  });

  it("total과 어긋나도 빈 응답에서 멈춘다(무한 루프 금지)", async () => {
    const calls: number[] = [];
    const fetchPage = async (offset: number): Promise<PdfLabelsResponse> => {
      calls.push(offset);
      return {
        items: offset === 0 ? [stubItem(0)] : [],
        total: 999, edits_version: 1, stale: false, dangling: [], unresolved: [],
      };
    };
    expect((await collectAllLabels(fetchPage)).items).toHaveLength(1);
    expect(calls).toEqual([0, 1]);
  });
});

describe("pdfApi helpers", () => {
  it("active statuses are the four in-flight ones", () => {
    for (const s of ["queued", "extracting", "translating", "overlaying"]) {
      expect(isActivePdfStatus(s)).toBe(true);
    }
    for (const s of ["done", "error", "cancelled"]) {
      expect(isActivePdfStatus(s)).toBe(false);
    }
  });

  it("pdfPageUrl encodes variant", () => {
    expect(pdfPageUrl("abc", 3, "translated")).toContain(
      "/api/v1/pdf-jobs/abc/page/3?variant=translated");
  });
});
