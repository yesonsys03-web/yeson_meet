import { describe, expect, it } from "vitest";
import {
  activeSegmentIndex, engineLabel, isSourceCopy, overlayStyleFor, sanitizeFilename,
  shouldShowCudaWarning,
} from "./videoReviewLogic";

const segs = [
  { start_ms: 0, end_ms: 1000 },
  { start_ms: 1500, end_ms: 3000 },
];

describe("activeSegmentIndex", () => {
  it("returns segment covering current time", () => {
    expect(activeSegmentIndex(segs, 500)).toBe(0);
    expect(activeSegmentIndex(segs, 2000)).toBe(1);
  });
  it("returns -1 in gaps and past end", () => {
    expect(activeSegmentIndex(segs, 1200)).toBe(-1);
    expect(activeSegmentIndex(segs, 99999)).toBe(-1);
  });
});

describe("overlayStyleFor", () => {
  it("bottom position anchors bottom with marginV", () => {
    const s = overlayStyleFor({ position: "bottom", margin_v: 40, font_size: 18, color: "#ffffff" });
    expect(s.bottom).toBe(40);
    expect(s.top).toBeUndefined();
    expect(s.fontSize).toBe(18);
  });
  it("top position anchors top", () => {
    const s = overlayStyleFor({ position: "top", margin_v: 20, font_size: 24, color: "#ffffff" });
    expect(s.top).toBe(20);
    expect(s.bottom).toBeUndefined();
  });
  it("scales font and margin by renderedHeight/288 (libass PlayResY)", () => {
    const s = overlayStyleFor({ position: "bottom", margin_v: 40, font_size: 18, color: "#ffffff" }, 576);
    expect(s.fontSize).toBe(36);
    expect(s.bottom).toBe(80);
  });
  it("applies the selected color to the overlay text", () => {
    const s = overlayStyleFor({ position: "bottom", margin_v: 40, font_size: 18, color: "#ff0000" });
    expect(s.color).toBe("#ff0000");
  });
});

describe("sanitizeFilename", () => {
  it("replaces each path-unsafe character with a dash", () => {
    expect(sanitizeFilename("Rig/Puppet")).toBe("Rig-Puppet");
    expect(sanitizeFilename('a:b*c?d"e<f>g|h\\i')).toBe("a-b-c-d-e-f-g-h-i");
  });
  it("leaves ordinary titles untouched", () => {
    expect(sanitizeFilename("meeting-2026-07-06")).toBe("meeting-2026-07-06");
  });
  it("strips trailing dots/spaces (Windows) and falls back when empty", () => {
    expect(sanitizeFilename("FINAL_LOCK_V02. ")).toBe("FINAL_LOCK_V02");
    expect(sanitizeFilename("...")).toBe("video");
  });
  it("keeps Korean/unicode titles as-is (no encoding munging)", () => {
    expect(sanitizeFilename("7월 회의 하이라이트")).toBe("7월 회의 하이라이트");
  });
});

describe("shouldShowCudaWarning", () => {
  it("warns only when enabled+installed but CUDA is not ok", () => {
    expect(shouldShowCudaWarning({ enabled: true, installed: true, cuda_ok: false })).toBe(true);
  });
  it("stays quiet when CUDA is ok", () => {
    expect(shouldShowCudaWarning({ enabled: true, installed: true, cuda_ok: true })).toBe(false);
  });
  it("stays quiet when GPU is disabled (other UI already explains)", () => {
    expect(shouldShowCudaWarning({ enabled: false, installed: true, cuda_ok: false })).toBe(false);
  });
  it("stays quiet when the pack isn't installed (other UI already explains)", () => {
    expect(shouldShowCudaWarning({ enabled: true, installed: false, cuda_ok: false })).toBe(false);
  });
  it("stays quiet when cuda_ok is undefined (older server response — no spurious warning)", () => {
    expect(shouldShowCudaWarning({ enabled: true, installed: true, cuda_ok: undefined })).toBe(false);
  });
});

describe("isSourceCopy", () => {
  const src = "Margarita vibes, baby girl!";

  it("원문을 그대로 복사한 줄을 잡는다", () => {
    expect(isSourceCopy(src, src)).toBe(true);
    expect(isSourceCopy(src, `  ${src}  `)).toBe(true);
  });

  it("정상 번역은 통과시킨다", () => {
    expect(isSourceCopy(src, "마르가리타 분위기야, 자기!")).toBe(false);
  });

  it("사용자가 일부러 영문으로 남긴 편집은 대상이 아니다", () => {
    // 서버 대상 선정과 같은 규칙이어야 배지 수 == 실제 고쳐질 수.
    expect(isSourceCopy(src, "Margarita mood, girl!")).toBe(false);
  });

  it("의도적으로 비운 줄은 건드리지 않는다", () => {
    expect(isSourceCopy(src, "")).toBe(false);
    expect(isSourceCopy(src, "   ")).toBe(false);
  });
});

describe("engineLabel", () => {
  it("reason이 있으면 reason을 붙인다 — '번역: ' 접두는 붙이지 않는다", () => {
    expect(engineLabel({ value: "qwen_x", label: "Qwen 12B", available: false,
                         reason: "실리콘맥 전용" })).toBe("Qwen 12B (실리콘맥 전용)");
  });
  it("gemini 미가용은 키 없음으로 구분한다", () => {
    expect(engineLabel({ value: "gemini", label: "Gemini", available: false }))
      .toBe("Gemini (서버에 키 없음)");
  });
  it("그 외 미가용은 미설치", () => {
    expect(engineLabel({ value: "claude", label: "Claude 구독", available: false }))
      .toBe("Claude 구독 (서버에 미설치)");
  });
  it("가용하면 라벨 그대로", () => {
    expect(engineLabel({ value: "claude", label: "Claude 구독", available: true }))
      .toBe("Claude 구독");
  });
});
