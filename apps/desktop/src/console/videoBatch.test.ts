import { describe, expect, it, vi } from "vitest";

import {
  abortBatchThenCancelAll, filterVideoFiles, uploadBatch, uploadBatchNative,
} from "./videoBatch";

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
    expect(res).toEqual({ ok: 2, failed: [], skipped: 0 });
    expect(calls).toEqual(["a.mp4|small|a.mp4|claude", "b.mp4|small|b.mp4|claude"]);
  });

  it("stops uploading remaining files once isCancelled() reports true, and reports skipped count", async () => {
    const calls: string[] = [];
    let cancelled = false;
    const upload = vi.fn(async (file: File) => {
      calls.push(file.name);
      if (file.name === "b.mp4") cancelled = true; // 사용자가 두 번째 업로드 중 전체 취소를 누른 상황 시뮬레이션
      return { job_id: file.name };
    });
    const res = await uploadBatch(
      [f("a.mp4"), f("b.mp4"), f("c.mp4"), f("d.mp4")],
      { whisperModel: "small" },
      upload,
      undefined,
      { isCancelled: () => cancelled },
    );
    expect(calls).toEqual(["a.mp4", "b.mp4"]); // c.mp4/d.mp4는 시작조차 안 됨
    expect(res).toEqual({ ok: 2, failed: [], skipped: 2 });
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

describe("abortBatchThenCancelAll", () => {
  it("waits for the in-flight batch to settle before calling cancel-all (누락 경합 제거)", async () => {
    const events: string[] = [];
    let cancelled = false;
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const upload = vi.fn(async (file: File) => {
      events.push(`start:${file.name}`);
      await gate; // 첫 업로드가 in-flight인 상태를 재현
      events.push(`end:${file.name}`);
      return { job_id: file.name };
    });
    const batch = uploadBatch(
      [f("a.mp4"), f("b.mp4")], { whisperModel: "small" }, upload,
      undefined, { isCancelled: () => cancelled },
    );
    const seq = abortBatchThenCancelAll(
      () => { cancelled = true; events.push("abort"); },
      batch,
      async () => { events.push("cancelAll"); },
    );
    release();
    await seq;
    // cancel-all은 반드시 진행 중이던 업로드가 끝난(배치 settle) 뒤에 호출된다 —
    // 취소 직후 완료된 업로드가 cancel-all을 비껴가는 누락이 구조적으로 없다.
    expect(events).toEqual(["start:a.mp4", "abort", "end:a.mp4", "cancelAll"]);
    expect(await batch).toEqual({ ok: 1, failed: [], skipped: 1 });
  });

  it("calls cancel-all immediately when no batch is running", async () => {
    const events: string[] = [];
    await abortBatchThenCancelAll(
      () => events.push("abort"),
      null,
      async () => { events.push("cancelAll"); },
    );
    expect(events).toEqual(["abort", "cancelAll"]);
  });

  it("still calls cancel-all when the pending batch rejects", async () => {
    const events: string[] = [];
    await abortBatchThenCancelAll(
      () => events.push("abort"),
      Promise.reject(new Error("boom")),
      async () => { events.push("cancelAll"); },
    );
    expect(events).toEqual(["abort", "cancelAll"]);
  });
});

describe("uploadBatchNative", () => {
  it("업로드 경로 배치 — 실패 무중단 순차 처리 + 진행 콜백", async () => {
    const entries = [
      { path: "/v/a.mp4", name: "a.mp4" },
      { path: "/v/b.mov", name: "b.mov" },
      { path: "/v/c.mkv", name: "c.mkv" },
    ];
    const cfg = { whisperModel: "small" };
    const calls: string[] = [];
    const progress: Array<[number, number, string]> = [];
    const res = await uploadBatchNative(entries, cfg, async (e, c) => {
      calls.push(`${e.path}:${c.whisperModel}`);
      if (e.name === "b.mov") throw new Error("boom");
    }, (d, t, cur) => progress.push([d, t, cur]));

    expect(res.ok).toBe(2);
    expect(res.failed).toEqual([{ name: "b.mov", error: "boom" }]);
    expect(calls).toEqual(["/v/a.mp4:small", "/v/b.mov:small", "/v/c.mkv:small"]);
    expect(progress[0]).toEqual([0, 3, "a.mp4"]);
    expect(progress.at(-1)).toEqual([3, 3, ""]);
  });
});
