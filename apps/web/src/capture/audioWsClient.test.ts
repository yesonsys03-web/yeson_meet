// === ANCHOR: AUDIO_WS_CLIENT_TEST_START ===
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AudioWsClient, type AudioWsStatus } from "./audioWsClient";

class FakeWs {
  static instances: FakeWs[] = [];
  sent: (string | Uint8Array)[] = [];
  readyState = 0; // CONNECTING
  onopen: (() => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeWs.instances.push(this);
  }
  send(data: string | Uint8Array) {
    this.sent.push(data);
  }
  close() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onclose?.({ code: 1000 });
  }
  serverOpen() {
    this.readyState = 1;
    this.onopen?.();
  }
  serverClose(code: number) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

function lastWs(): FakeWs {
  return FakeWs.instances[FakeWs.instances.length - 1];
}

describe("AudioWsClient", () => {
  let statuses: AudioWsStatus[];
  let client: AudioWsClient;

  beforeEach(() => {
    vi.useFakeTimers();
    FakeWs.instances = [];
    statuses = [];
    client = new AudioWsClient("ws://x/ws/sidecar?key=k&session=s", (s) => statuses.push(s), (url) => new FakeWs(url) as never);
  });
  afterEach(() => vi.useRealTimers());

  it("open 시 audio.started가 첫 메시지, 이후 바이너리", () => {
    client.start();
    client.sendChunk(new Uint8Array(640)); // 연결 전 → 드롭
    lastWs().serverOpen();
    client.sendChunk(new Uint8Array(640));
    const sent = lastWs().sent;
    expect(JSON.parse(sent[0] as string).type).toBe("audio.started");
    expect(JSON.parse(sent[0] as string).sample_rate).toBe(16000);
    expect(sent[1]).toBeInstanceOf(Uint8Array);
    expect(sent.length).toBe(2); // 연결 전 청크는 드롭됨
  });

  it("청크 50개마다 chunk_meta", () => {
    client.start();
    lastWs().serverOpen();
    for (let i = 0; i < 50; i++) client.sendChunk(new Uint8Array(640));
    const texts = lastWs().sent.filter((d): d is string => typeof d === "string");
    const meta = texts.map((t) => JSON.parse(t)).filter((m) => m.type === "chunk_meta");
    expect(meta.length).toBe(1);
    expect(meta[0].seq).toBe(50);
  });

  it("stop은 audio.stopped를 보내고 닫는다", () => {
    client.start();
    lastWs().serverOpen();
    client.stop("user stop");
    const texts = lastWs().sent.filter((d): d is string => typeof d === "string");
    const stopped = texts.map((t) => JSON.parse(t)).find((m) => m.type === "audio.stopped");
    expect(stopped.reason).toBe("user stop");
    expect(statuses.at(-1)).toBe("stopped");
  });

  it("끊기면 백오프 재접속하고 audio.started 재전송", () => {
    client.start();
    lastWs().serverOpen();
    vi.advanceTimersByTime(5000); // 안정 연결로 인정된 후
    lastWs().serverClose(1006);
    expect(statuses.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(1000); // backoff 1s
    expect(FakeWs.instances.length).toBe(2);
    lastWs().serverOpen();
    expect(JSON.parse(lastWs().sent[0] as string).type).toBe("audio.started");
    expect(statuses.at(-1)).toBe("streaming");
  });

  it("open 직후 닫힘 3연속이면 rejected로 멈춘다", () => {
    client.start();
    for (let i = 0; i < 3; i++) {
      lastWs().serverOpen();
      lastWs().serverClose(1008); // 즉시 거부
      vi.advanceTimersByTime(30000);
    }
    expect(statuses.at(-1)).toBe("rejected");
    expect(FakeWs.instances.length).toBe(3); // 더 이상 재시도 없음
  });
});
// === ANCHOR: AUDIO_WS_CLIENT_TEST_END ===
