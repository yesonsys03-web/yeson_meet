import { describe, it, expect, beforeEach } from "vitest";
import { SubtitleTimingRecorder } from "./subtitleTiming";

describe("SubtitleTimingRecorder", () => {
  let recorder: SubtitleTimingRecorder;
  beforeEach(() => {
    recorder = new SubtitleTimingRecorder(() => 1000);
  });

  it("records arrival timestamp per seq", () => {
    recorder.markArrival({ seq: 1, isFinal: false });
    recorder.markArrival({ seq: 1, isFinal: true });
    const events = recorder.export();
    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({ seq: 1, isFinal: false, t_ms: 1000 });
    expect(events[1]).toMatchObject({ seq: 1, isFinal: true });
  });

  it("exports as downloadable JSON string", () => {
    recorder.markArrival({ seq: 1, isFinal: true });
    const json = recorder.toJSON();
    const parsed = JSON.parse(json);
    expect(parsed.events).toHaveLength(1);
    expect(parsed.recorded_at).toBeDefined();
  });

  it("reset clears events", () => {
    recorder.markArrival({ seq: 1, isFinal: true });
    recorder.reset();
    expect(recorder.export()).toHaveLength(0);
  });
});
