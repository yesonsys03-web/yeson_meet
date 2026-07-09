// === ANCHOR: AUDIO_WS_CLIENT_START ===
// /ws/sidecar 전송 계약 구현 (apps/client_sidecar/transport/audio_ws.py의 웹 포팅).
// 계약: audio.started가 항상 첫 메시지 → 바이너리 PCM → 50청크마다 chunk_meta →
// 종료 시 audio.stopped. 서버는 거부 시 reason 없는 1008로 닫으므로
// "open 직후(2s 내) 닫힘 3연속"을 거부로 해석한다.
export type AudioWsStatus = "idle" | "connecting" | "streaming" | "reconnecting" | "rejected" | "stopped";

export type WebSocketLike = {
  send(data: string | Uint8Array): void;
  close(): void;
  readyState: number;
  onopen: (() => void) | null;
  onclose: ((e: { code: number }) => void) | null;
  onerror: (() => void) | null;
};

const CHUNK_META_INTERVAL = 50;
const REJECT_WINDOW_MS = 2000;
const REJECT_LIMIT = 3;
const MAX_BACKOFF_MS = 30000;

export class AudioWsClient {
  private ws: WebSocketLike | null = null;
  private status: AudioWsStatus = "idle";
  private seq = 0;
  private droppedChunks = 0;
  private backoffMs = 1000;
  private consecutiveRejects = 0;
  private openedAt = 0;
  private stopping = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly url: string,
    private readonly onStatus: (status: AudioWsStatus) => void,
    private readonly wsFactory: (url: string) => WebSocketLike = (u) => new WebSocket(u) as unknown as WebSocketLike,
  ) {}

  get dropped(): number {
    return this.droppedChunks;
  }

  start(): void {
    if (this.status !== "idle") return;
    this.connect("connecting");
  }

  sendChunk(chunk: Uint8Array): void {
    if (this.status !== "streaming" || !this.ws || this.ws.readyState !== 1) {
      this.droppedChunks += 1;
      return;
    }
    this.seq += 1;
    this.ws.send(chunk);
    if (this.seq % CHUNK_META_INTERVAL === 0) {
      this.ws.send(JSON.stringify({ type: "chunk_meta", seq: this.seq, started_at: new Date().toISOString() }));
    }
  }

  stop(reason: string | null): void {
    this.stopping = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.ws && this.ws.readyState === 1) {
      try {
        this.ws.send(JSON.stringify({ type: "audio.stopped", reason }));
      } catch {}
    }
    this.ws?.close();
    this.setStatus("stopped");
  }

  private connect(entryStatus: AudioWsStatus): void {
    this.setStatus(entryStatus);
    const ws = this.wsFactory(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.openedAt = Date.now();
      ws.send(
        JSON.stringify({
          type: "audio.started",
          sample_rate: 16000,
          channels: 1,
          format: "pcm_s16le",
          started_at: new Date().toISOString(),
        }),
      );
      this.setStatus("streaming");
    };
    ws.onerror = () => {};
    ws.onclose = () => {
      if (this.stopping) return;
      const openMs = this.openedAt ? Date.now() - this.openedAt : 0;
      this.openedAt = 0;
      if (openMs < REJECT_WINDOW_MS) {
        this.consecutiveRejects += 1;
      } else if (openMs >= REJECT_WINDOW_MS) {
        this.consecutiveRejects = 0;
        this.backoffMs = 1000;
      }
      if (this.consecutiveRejects >= REJECT_LIMIT) {
        this.setStatus("rejected");
        return;
      }
      this.setStatus("reconnecting");
      this.reconnectTimer = setTimeout(() => this.connect("reconnecting"), this.backoffMs);
      this.backoffMs = Math.min(this.backoffMs * 2, MAX_BACKOFF_MS);
    };
  }

  private setStatus(status: AudioWsStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.onStatus(status);
  }
}
// === ANCHOR: AUDIO_WS_CLIENT_END ===
