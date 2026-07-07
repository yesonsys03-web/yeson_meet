import { describe, expect, it, vi } from "vitest";

import { filterVideoFiles, uploadBatch } from "./videoBatch";

function f(name: string): File {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "application/octet-stream" });
}

describe("filterVideoFiles", () => {
  it("keeps common video extensions, drops the rest (case-insensitive)", () => {
    const files = [f("a.mp4"), f("b.MOV"), f("notes.txt"), f("c.mkv"), f("thumb.jpg"), f("noext")];
    const kept = filterVideoFiles(files).map((x) => x.name);
    expect(kept).toEqual(["a.mp4", "b.MOV", "c.mkv"]);
  });
});

describe("uploadBatch", () => {
  it("uploads every file sequentially with the shared config, in order", async () => {
    const calls: string[] = [];
    const upload = vi.fn(async (file: File, model: string, title: string, tp?: string) => {
      calls.push(`${file.name}|${model}|${title}|${tp ?? "-"}`);
      return { job_id: file.name };
    });
    const res = await uploadBatch(
      [f("a.mp4"), f("b.mp4")],
      { whisperModel: "small", translateProvider: "claude" },
      upload,
    );
    expect(res).toEqual({ ok: 2, failed: [] });
    expect(calls).toEqual(["a.mp4|small|a.mp4|claude", "b.mp4|small|b.mp4|claude"]);
  });

  it("continues past a failing file and reports it (batch not aborted)", async () => {
    const upload = vi.fn(async (file: File) => {
      if (file.name === "bad.mp4") throw new Error("disk full");
      return { job_id: file.name };
    });
    const res = await uploadBatch([f("a.mp4"), f("bad.mp4"), f("c.mp4")], { whisperModel: "small" }, upload);
    expect(res.ok).toBe(2);
    expect(res.failed).toEqual([{ name: "bad.mp4", error: "disk full" }]);
    expect(upload).toHaveBeenCalledTimes(3);
  });

  it("reports progress per file and a final completion tick", async () => {
    const ticks: Array<[number, number]> = [];
    await uploadBatch(
      [f("a.mp4"), f("b.mp4")],
      { whisperModel: "small" },
      async () => ({ job_id: "x" }),
      (done, total) => ticks.push([done, total]),
    );
    expect(ticks).toEqual([[0, 2], [1, 2], [2, 2]]);
  });
});
