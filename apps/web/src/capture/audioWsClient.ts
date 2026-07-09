// === ANCHOR: AUDIO_WS_CLIENT_START ===
// /ws/sidecar 전송 계약 구현 (apps/client_sidecar/transport/audio_ws.py의 웹 포팅).
// 계약: audio.started가 항상 첫 메시지 → 바이너리 PCM → 50청크마다 chunk_meta →
// 종료 시 audio.stopped. 접속 시점 거부(키 무효/세션 종료/타 디바이스 점유)는
// 핸드셰이크 거부(HTTP 403)라 open 이벤트 없이 close만 관측된다 — 브라우저에는
// "open 직후 닫힘"으로 보이지 않는다. open 후 짧게 닫히는 것은 스트림 중 정책
// 종료(최대 회의시간 등)에서만 발생하므로 "open 직후(2s 내) 닫힘 3연속"을 거부로
// 해석한다. "unreachable"은 서버 다운과 접속 거부(핸드셰이크 실패)를 구분할 수
// 없으므로 재시도는 유지한 채 경고만 표면화한다(open 없는 close가 5회 연속되면 진입).
export type AudioWsStatus =
  | "idle"
  | "connecting"
  | "streaming"
  | "reconnecting"
  | "rejected"
  | "unreachable"
  | "stopped";

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
const OPENLESS_CLOSE_LIMIT = 5;

export class AudioWsClient {
  private ws: WebSocketLike | null = null;
  private status: AudioWsStatus = "idle";
  private seq = 0;
  private droppedChunks = 0;
  private backoffMs = 1000;
  private consecutiveRejects = 0;
  private consecutiveOpenlessCloses = 0;
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
      this.consecutiveOpenlessCloses = 0;
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
      // 거부 판정은 "실제로 open됐다가 2s 내에 닫힌" 경우만. open조차 못 한
      // close(서버 다운·터널 blip·핸드셰이크 거부)는 거부 카운트/리셋 없이
      // 백오프 재시도만 하되, 5회 연속되면 "unreachable"로 경고를 표면화한다.
      const hadOpened = this.openedAt > 0;
      const openMs = hadOpened ? Date.now() - this.openedAt : 0;
      this.openedAt = 0;
      if (hadOpened && openMs < REJECT_WINDOW_MS) {
        this.consecutiveRejects += 1;
      } else if (hadOpened) {
        this.consecutiveRejects = 0;
        this.backoffMs = 1000;
      }
      if (!hadOpened) {
        this.consecutiveOpenlessCloses += 1;
      }
      if (this.consecutiveRejects >= REJECT_LIMIT) {
        this.setStatus("rejected");
        return;
      }
      // unreachable 상태에서 재시도가 계속되는 동안은 매 시도마다 "reconnecting"으로
      // 되돌리지 않는다(setStatus가 동일 상태 재설정을 무시하므로 계속 unreachable 유지).
      const nextStatus: AudioWsStatus =
        this.consecutiveOpenlessCloses >= OPENLESS_CLOSE_LIMIT ? "unreachable" : "reconnecting";
      this.setStatus(nextStatus);
      this.reconnectTimer = setTimeout(() => this.connect(nextStatus), this.backoffMs);
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
