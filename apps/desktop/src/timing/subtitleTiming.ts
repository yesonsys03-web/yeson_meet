// === ANCHOR: SUBTITLE_TIMING_START ===
export interface SubtitleArrival {
  seq: number;
  isFinal: boolean;
}

interface TimingEvent extends SubtitleArrival {
  t_ms: number;
}

export class SubtitleTimingRecorder {
  private events: TimingEvent[] = [];
  private readonly now: () => number;

  constructor(now: () => number = () => performance.now()) {
    this.now = now;
  }

  markArrival(arrival: SubtitleArrival): void {
    this.events.push({ ...arrival, t_ms: this.now() });
  }

  export(): TimingEvent[] {
    return [...this.events];
  }

  toJSON(): string {
    return JSON.stringify(
      { recorded_at: new Date().toISOString(), events: this.events },
      null,
      2,
    );
  }

  reset(): void {
    this.events = [];
  }
}
// === ANCHOR: SUBTITLE_TIMING_END ===
