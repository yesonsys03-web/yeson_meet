// === ANCHOR: USE_CAPTURE_SESSION_START ===
// /capture 페이지 오케스트레이션: 로그인 → 준비 → 캡처 중 → 종료.
// 디바이스 키는 브라우저 localStorage에 1개(없거나 서버가 거부하면 자가등록).
import { useCallback, useRef, useState } from "react";
import { PcmFramer, pcm16Dbfs } from "./pcm";
import {
  createCaptureSession,
  credentialStore,
  endCaptureSession,
  loginOperator,
  selfEnrollDevice,
  sidecarWsUrl,
} from "./captureApi";
import { AudioWsClient, type AudioWsStatus } from "./audioWsClient";
import { NoTabAudioError, startTabCapture, type TabCaptureHandle } from "./tabCapture";

export type CapturePhase = "login" | "ready" | "capturing" | "ended";

export type CaptureSessionState = {
  phase: CapturePhase;
  busy: boolean;
  error: string | null;
  operatorToken: string | null;
  sessionId: string | null;
  viewerUrl: string | null;
  title: string;
  wsStatus: AudioWsStatus;
  levelDbfs: number; // UI용, -120..0
  micOn: boolean;
  captureLost: boolean; // 공유 중지/탭 닫힘 — 회의는 유지, 재선택 필요
};

export type CaptureSessionActions = {
  login(email: string, password: string): Promise<void>;
  setTitle(title: string): void;
  startMeeting(): Promise<void>;
  startCapture(): Promise<void>;
  toggleMic(): Promise<void>;
  stopCaptureAndEnd(): Promise<void>;
  resetError(): void;
};

const LEVEL_UPDATE_EVERY_CHUNKS = 5; // 100ms

export function useCaptureSession(): CaptureSessionState & CaptureSessionActions {
  const [state, setState] = useState<CaptureSessionState>({
    phase: "login",
    busy: false,
    error: null,
    operatorToken: null,
    sessionId: null,
    viewerUrl: null,
    title: "",
    wsStatus: "idle",
    levelDbfs: -120,
    micOn: false,
    captureLost: false,
  });
  const wsClientRef = useRef<AudioWsClient | null>(null);
  const tabRef = useRef<TabCaptureHandle | null>(null);
  const framerRef = useRef<PcmFramer | null>(null);
  const chunkCountRef = useRef(0);
  const store = credentialStore();

  const patch = useCallback((p: Partial<CaptureSessionState>) => setState((s) => ({ ...s, ...p })), []);

  const login = useCallback(
    async (email: string, password: string) => {
      patch({ busy: true, error: null });
      try {
        const tokens = await loginOperator(email, password);
        patch({ busy: false, operatorToken: tokens.access_token, phase: "ready" }); // vibelign: allow-secret
      } catch (e) {
        patch({ busy: false, error: `로그인 실패: ${e instanceof Error ? e.message : String(e)}` });
      }
    },
    [patch],
  );

  const setTitle = useCallback((title: string) => patch({ title }), [patch]);

  const startMeeting = useCallback(async () => {
    if (!state.operatorToken || !state.title.trim()) return;
    patch({ busy: true, error: null });
    try {
      const created = await createCaptureSession(state.operatorToken, state.title.trim());
      patch({ busy: false, sessionId: created.session_id, viewerUrl: created.viewer_url });
    } catch (e) {
      patch({ busy: false, error: `회의 생성 실패: ${e instanceof Error ? e.message : String(e)}` });
    }
  }, [patch, state.operatorToken, state.title]);

  const ensureDeviceKey = useCallback(async (): Promise<string> => {
    const existing = store.loadDeviceKey();
    if (existing) return existing;
    const name = `web-capture-${Math.random().toString(36).slice(2, 6)}`;
    const key = await selfEnrollDevice(state.operatorToken!, name);
    store.saveDeviceKey(key);
    return key;
  }, [state.operatorToken, store]);

  const startCapture = useCallback(async () => {
    if (!state.sessionId || !state.operatorToken) return;
    patch({ busy: true, error: null, captureLost: false });
    try {
      const deviceKey = await ensureDeviceKey();
      const client = new AudioWsClient(sidecarWsUrl(deviceKey, state.sessionId), (wsStatus) => {
        patch({ wsStatus });
        if (wsStatus === "rejected") {
          // 거부 시 다음 시도에서 재등록되도록 키를 지운다(폐기된 키 대비).
          store.clearDeviceKey();
          patch({ error: "서버가 연결을 거부했습니다. 다른 기기가 이미 캡처 중이거나 세션이 종료됐거나 디바이스 키가 무효화됐을 수 있습니다. 잠시 후 다시 시도하세요." });
        }
      });
      framerRef.current = new PcmFramer();
      chunkCountRef.current = 0;
      const tab = await startTabCapture({
        onPcmBlock: (block) => {
          const chunks = framerRef.current?.push(block) ?? [];
          for (const chunk of chunks) {
            client.sendChunk(chunk);
            chunkCountRef.current += 1;
            if (chunkCountRef.current % LEVEL_UPDATE_EVERY_CHUNKS === 0) {
              const int16 = new Int16Array(chunk.buffer, chunk.byteOffset, chunk.byteLength / 2);
              patch({ levelDbfs: Math.max(-120, pcm16Dbfs(int16)) });
            }
          }
        },
        onEnded: () => {
          wsClientRef.current?.stop("capture surface ended");
          patch({ captureLost: true, wsStatus: "stopped", micOn: false });
        },
      });
      wsClientRef.current = client;
      tabRef.current = tab;
      client.start();
      patch({ busy: false, phase: "capturing" });
    } catch (e) {
      if (e instanceof NoTabAudioError) {
        patch({ busy: false, error: "선택한 화면에 오디오가 없습니다. 구글밋이 열린 '탭'을 선택하고 '탭 오디오 공유'를 체크한 뒤 다시 시도하세요." });
      } else {
        patch({ busy: false, error: `캡처 시작 실패: ${e instanceof Error ? e.message : String(e)}` });
      }
    }
  }, [ensureDeviceKey, patch, state.operatorToken, state.sessionId, store]);

  const toggleMic = useCallback(async () => {
    const tab = tabRef.current;
    if (!tab) return;
    try {
      if (tab.micAttached()) {
        tab.detachMic();
        patch({ micOn: false });
      } else {
        await tab.attachMic();
        patch({ micOn: true });
      }
    } catch (e) {
      patch({ error: `마이크 사용 실패: ${e instanceof Error ? e.message : String(e)}` });
    }
  }, [patch]);

  const stopCaptureAndEnd = useCallback(async () => {
    patch({ busy: true, error: null });
    tabRef.current?.stop();
    tabRef.current = null;
    wsClientRef.current?.stop("user stop");
    wsClientRef.current = null;
    try {
      if (state.operatorToken && state.sessionId) await endCaptureSession(state.operatorToken, state.sessionId);
      patch({ busy: false, phase: "ended", micOn: false });
    } catch (e) {
      patch({ busy: false, phase: "ended", micOn: false, error: `회의 종료 API 실패(자막은 중단됨): ${e instanceof Error ? e.message : String(e)}` });
    }
  }, [patch, state.operatorToken, state.sessionId]);

  const resetError = useCallback(() => patch({ error: null }), [patch]);

  return { ...state, login, setTitle, startMeeting, startCapture, toggleMic, stopCaptureAndEnd, resetError };
}
// === ANCHOR: USE_CAPTURE_SESSION_END ===
