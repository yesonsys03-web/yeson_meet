import { describe, expect, it } from "vitest";

import { toEngineOptions } from "./VideoCaptionPanel";
import type { TranslateEngineInfo } from "./videoApi";

describe("toEngineOptions", () => {
  it("reason이 있으면 '(서버에 미설치)' 대신 reason을 라벨에 붙인다", () => {
    const engines: TranslateEngineInfo[] = [
      { value: "qwen_x", label: "Qwen 12B", available: false, reason: "실리콘맥 전용" },
    ];
    const opt = toEngineOptions(engines)[0]!;
    expect(opt.label).toBe("번역: Qwen 12B (실리콘맥 전용)");
    expect(opt.label).not.toContain("서버에 미설치");
  });

  it("reason이 없으면 기존처럼 '(서버에 미설치)'를 붙인다", () => {
    const engines: TranslateEngineInfo[] = [
      { value: "claude", label: "Claude 구독", available: false },
    ];
    const opt = toEngineOptions(engines)[0]!;
    expect(opt.label).toBe("번역: Claude 구독 (서버에 미설치)");
  });
});
