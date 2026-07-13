import { describe, expect, it } from "vitest";

import {
  actionableJobIds, canRebuild, captionedFileName, overallProgress, partitionSelection,
} from "./videoBatchOps";
import type { VideoJobSummary } from "./videoApi";

function job(job_id: string, status: string): VideoJobSummary {
  return {
    job_id, title: `t-${job_id}`, source_type: "upload", source_ref: "x.mp4",
    whisper_model: "base", translate_provider: null, status, progress: 0,
    error: null, created_at: null,
  };
}

const JOBS = [
  job("a", "review"),
  job("b", "done"),
  job("c", "transcribing"),
  job("d", "review"),
  job("e", "error"),
  job("f", "done"),
  job("g", "cancelled"),
];

describe("actionableJobIds", () => {
  it("returns terminal jobs incl. error/cancelled (excludes in-flight only)", () => {
    expect(actionableJobIds(JOBS)).toEqual(["a", "b", "d", "e", "f", "g"]);
  });
});

describe("canRebuild", () => {
  it("allows rebuild from terminal states including cancelled (error와 동일 취급)", () => {
    for (const s of ["review", "done", "error", "cancelled"]) {
      expect(canRebuild(s)).toBe(true);
    }
  });

  it("rejects in-flight states", () => {
    for (const s of ["queued", "ingesting", "extracting", "transcribing", "translating", "burning"]) {
      expect(canRebuild(s)).toBe(false);
    }
  });
});

describe("overallProgress", () => {
  it("maps fixed stages to their baselines", () => {
    expect(overallProgress("queued", 0)).toBe(0);
    expect(overallProgress("ingesting", 0)).toBe(5);
    expect(overallProgress("extracting", 0)).toBe(15);
    expect(overallProgress("review", 0)).toBe(100);
    expect(overallProgress("done", 42)).toBe(100);
    expect(overallProgress("error", 77)).toBe(0);
    expect(overallProgress("cancelled", 77)).toBe(0);
  });

  it("scales instrumented stages into their overall band", () => {
    expect(overallProgress("transcribing", 0)).toBe(15);
    expect(overallProgress("transcribing", 50)).toBe(38); // 15 + 22.5 → round
    expect(overallProgress("transcribing", 100)).toBe(60);
    expect(overallProgress("translating", 0)).toBe(60);
    expect(overallProgress("translating", 100)).toBe(80);
    expect(overallProgress("burning", 0)).toBe(80);
    expect(overallProgress("burning", 50)).toBe(90);
    expect(overallProgress("burning", 100)).toBe(100);
  });

  it("is monotonic across stage transitions (bar never goes backwards)", () => {
    // 단계 전환 시퀀스: 각 단계 끝(100) → 다음 단계 시작(0)이 절대 감소하지 않는다.
    const seq: Array<[string, number]> = [
      ["queued", 0], ["ingesting", 0], ["extracting", 0],
      ["transcribing", 0], ["transcribing", 100],
      ["translating", 0], ["translating", 100],
      ["burning", 0], ["burning", 100],
      ["done", 0],
    ];
    let prev = -1;
    for (const [status, p] of seq) {
      const cur = overallProgress(status, p);
      expect(cur).toBeGreaterThanOrEqual(prev);
      prev = cur;
    }
  });

  it("clamps out-of-range raw progress to 0..100", () => {
    expect(overallProgress("transcribing", 150)).toBe(60);
    expect(overallProgress("transcribing", -20)).toBe(15);
    expect(overallProgress("unknown-status", 50)).toBe(0);
  });
});

describe("captionedFileName", () => {
  it("strips the original video extension and appends _KO.mp4", () => {
    expect(captionedFileName("TJS101_Animatic_LOCK_LABELED_20251003_RL.mp4"))
      .toBe("TJS101_Animatic_LOCK_LABELED_20251003_RL_KO.mp4");
  });

  it("works when the title has no extension", () => {
    expect(captionedFileName("클립1")).toBe("클립1_KO.mp4");
  });

  it("handles other video extensions case-insensitively", () => {
    expect(captionedFileName("A.MOV")).toBe("A_KO.mp4");
    expect(captionedFileName("b.mkv")).toBe("b_KO.mp4");
  });

  it("sanitizes illegal filename characters", () => {
    expect(captionedFileName("a/b:c.mp4")).toBe("a-b-c_KO.mp4");
  });
});

describe("partitionSelection", () => {
  it("splits the selected set into burnable(review) and downloadable(done)", () => {
    const sel = new Set(["a", "b", "d"]);
    const { burnable, downloadable, rebuildable } = partitionSelection(JOBS, sel);
    expect(burnable.map((j) => j.job_id)).toEqual(["a", "d"]);
    expect(downloadable.map((j) => j.job_id)).toEqual(["b"]);
    expect(rebuildable).toEqual([]);
  });

  it("routes selected error/cancelled jobs to rebuildable (in-flight ignored)", () => {
    const sel = new Set(["c", "e", "g"]); // transcribing + error + cancelled
    const { burnable, downloadable, rebuildable } = partitionSelection(JOBS, sel);
    expect(burnable).toEqual([]);
    expect(downloadable).toEqual([]);
    expect(rebuildable.map((j) => j.job_id)).toEqual(["e", "g"]);
  });

  it("ignores ids not present in the job list", () => {
    const sel = new Set(["zzz"]);
    const { burnable, downloadable, rebuildable } = partitionSelection(JOBS, sel);
    expect(burnable).toEqual([]);
    expect(downloadable).toEqual([]);
    expect(rebuildable).toEqual([]);
  });
});
