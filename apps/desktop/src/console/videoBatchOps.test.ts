import { describe, expect, it } from "vitest";

import { actionableJobIds, canRebuild, captionedFileName, partitionSelection } from "./videoBatchOps";
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
];

describe("actionableJobIds", () => {
  it("returns only review/done jobs (excludes in-flight and error)", () => {
    expect(actionableJobIds(JOBS)).toEqual(["a", "b", "d", "f"]);
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
    const { burnable, downloadable } = partitionSelection(JOBS, sel);
    expect(burnable.map((j) => j.job_id)).toEqual(["a", "d"]);
    expect(downloadable.map((j) => j.job_id)).toEqual(["b"]);
  });

  it("ignores selected ids whose status is not actionable", () => {
    const sel = new Set(["c", "e"]); // transcribing + error
    const { burnable, downloadable } = partitionSelection(JOBS, sel);
    expect(burnable).toEqual([]);
    expect(downloadable).toEqual([]);
  });

  it("ignores ids not present in the job list", () => {
    const sel = new Set(["zzz"]);
    const { burnable, downloadable } = partitionSelection(JOBS, sel);
    expect(burnable).toEqual([]);
    expect(downloadable).toEqual([]);
  });
});
