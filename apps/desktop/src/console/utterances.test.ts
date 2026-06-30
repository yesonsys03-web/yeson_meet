import { describe, expect, it } from "vitest";

import type { UtteranceTranscribed } from "./types";
import { upsertUtterance } from "./utterances";

function u(seq: number, text: string, is_final: boolean): UtteranceTranscribed {
  return {
    type: "utterance.transcribed",
    session_id: "s",
    occurred_at: "",
    seq,
    speaker: null,
    text_en: "",
    text_ko: text,
    started_at: "",
    ended_at: "",
    is_final,
  };
}

describe("upsertUtterance partial stability", () => {
  it("keeps the longer text when a shorter partial arrives for the same seq", () => {
    let list = upsertUtterance([], u(1, "클린업을 진행합니다", false));
    list = upsertUtterance(list, u(1, "클", false)); // non-incremental reset
    expect(list[0]?.text_ko).toBe("클린업을 진행합니다"); // not shrunk
  });

  it("accepts a longer partial (growth)", () => {
    let list = upsertUtterance([], u(1, "클린업", false));
    list = upsertUtterance(list, u(1, "클린업을 진행합니다", false));
    expect(list[0]?.text_ko).toBe("클린업을 진행합니다");
  });

  it("lets a final replace a longer partial (finals always win)", () => {
    let list = upsertUtterance([], u(1, "클린업을 진행합니다 그리고", false));
    list = upsertUtterance(list, u(1, "클린업.", true)); // shorter but final
    expect(list[0]?.text_ko).toBe("클린업.");
    expect(list[0]?.is_final).toBe(true);
  });

  it("still ignores a partial after a final", () => {
    let list = upsertUtterance([], u(1, "최종", true));
    list = upsertUtterance(list, u(1, "최종 더 길게", false));
    expect(list[0]?.text_ko).toBe("최종");
  });
});
