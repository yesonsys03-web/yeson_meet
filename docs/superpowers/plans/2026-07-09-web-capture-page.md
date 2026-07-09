# Web Capture Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 진행자가 설치 없이 브라우저에서 구글밋 탭 오디오를 캡처해 실시간 자막을 만드는 `/capture` 페이지를 `apps/web` SPA에 추가한다.

**Architecture:** 기존 뷰어 SPA(`apps/web`)에 `/capture` 라우트를 추가한다. 브라우저가 데스크탑 사이드카의 역할(캡처→16kHz mono s16le 변환→`/ws/sidecar`로 바이너리 스트리밍)을 대신한다. 서버는 변경하지 않는다 — 인증(운영자 로그인+디바이스 자가등록), 세션 생성/종료, 자막 스트림 모두 기존 API를 그대로 쓴다.

**Tech Stack:** React 18 + Vite + TypeScript + Tailwind (apps/web 기존 스택), Web Audio API(AudioContext/AudioWorklet), WebSocket, `qrcode`(신규 의존성), `vitest`(신규 devDependency).

**Spec:** `docs/superpowers/specs/2026-07-09-web-capture-page-design.md`

## Global Constraints

- 오디오 wire 포맷 고정: **16000Hz · mono · pcm_s16le**, 청크 640바이트(=320샘플=20ms). 서버(`apps/server/ws/sidecar.py`)는 이 포맷을 가정한다.
- WS 전송 순서 계약: `audio.started` JSON을 **모든 바이너리보다 먼저**. 텍스트 프레임은 `audio.started`/`chunk_meta`/`audio.stopped` 3종만 (그 외 텍스트를 보내면 서버가 1008로 닫음).
- 서버 코드(`apps/server/**`)와 뷰어 기존 코드(`SubtitleView`, `useViewerWS`, `usePacedSubtitle`)는 **수정 금지**. `App.tsx`는 라우트 분기 한 곳만 수정.
- 모든 새 파일은 이 저장소의 앵커 규약을 따른다: 파일 전체를 `// === ANCHOR: <NAME>_START ===` / `_END ===`로 감싼다 (기존 `apps/web/src/lib/api.ts` 참조).
- 커밋 메시지는 기존 스타일(`feat(web): …` 한국어 요약)을 따르고 다음 줄로 끝낸다: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- 패키지 매니저는 pnpm 워크스페이스. 의존성 추가 후 저장소 루트에서 `pnpm install`.
- 브랜치: `web_auto_update` (이미 체크아웃됨).

## File Structure

```
apps/web/src/capture/
  pcm.ts               # 순수: Float32→s16le 변환, 640B 프레이밍, dBFS 계산
  pcm.test.ts
  captureApi.ts        # REST(login/self-enroll/세션)·WS URL 빌더·키 저장소 (사이드이펙트는 fetch/storage뿐)
  captureApi.test.ts
  captureSupport.ts    # 순수: 보안컨텍스트/getDisplayMedia/Chromium 감지
  captureSupport.test.ts
  audioWsClient.ts     # /ws/sidecar 프로토콜 클라이언트(전송 순서·chunk_meta·백오프·거부 감지)
  audioWsClient.test.ts
  tabCapture.ts        # getDisplayMedia + AudioContext(16k) + AudioWorklet + 마이크 믹싱
  useOperatorSubtitles.ts  # /ws/operator 자막 미리보기 훅 (useViewerWS 패턴 이식)
  useCaptureSession.ts # 페이지 상태 오케스트레이션 훅
  CaptureView.tsx      # /capture 페이지 UI
apps/web/src/App.tsx   # 수정: /capture 라우트 분기 추가
apps/web/package.json  # 수정: qrcode/@types/qrcode/vitest 추가, test 스크립트
docs/web-capture-operator-guide.md  # 운영 가이드 + 릴리스 체크리스트 항목
```

---

### Task 1: 테스트 인프라 + PCM 순수 모듈

**Files:**
- Modify: `apps/web/package.json`
- Create: `apps/web/src/capture/pcm.ts`
- Test: `apps/web/src/capture/pcm.test.ts`

**Interfaces:**
- Consumes: 없음
- Produces: `TARGET_SAMPLE_RATE=16000`, `CHUNK_SAMPLES=320`, `CHUNK_BYTES=640`, `floatTo16le(samples: Float32Array): Int16Array`, `pcm16Dbfs(samples: Int16Array): number`, `class PcmFramer { push(block: Float32Array): Uint8Array[]; }` — Task 4·5·6이 사용.

- [ ] **Step 1: vitest 추가**

`apps/web/package.json`의 `scripts`에 `"test": "vitest run"`, `"test:watch": "vitest"`를 추가하고 `devDependencies`에 `"vitest": "^2.1.8"`을 추가한다(버전은 `apps/desktop/package.json`의 vitest와 동일하게 맞춘다 — 다르면 그쪽 값을 사용). 그 후:

```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet && pnpm install
```

Expected: lockfile 갱신, 설치 성공.

- [ ] **Step 2: 실패하는 테스트 작성** — `apps/web/src/capture/pcm.test.ts`

```typescript
// === ANCHOR: PCM_TEST_START ===
import { describe, expect, it } from "vitest";
import { CHUNK_BYTES, CHUNK_SAMPLES, PcmFramer, floatTo16le, pcm16Dbfs } from "./pcm";

describe("floatTo16le", () => {
  it("스케일과 클램프", () => {
    const out = floatTo16le(new Float32Array([0, 0.5, 1.0, -1.0, 2.0, -2.0]));
    expect(out[0]).toBe(0);
    expect(out[1]).toBe(16384); // round(0.5*32767)=16384
    expect(out[2]).toBe(32767);
    expect(out[3]).toBe(-32767);
    expect(out[4]).toBe(32767); // 클램프
    expect(out[5]).toBe(-32767);
  });
});

describe("pcm16Dbfs", () => {
  it("빈 입력은 -120 (사이드카 rms.py와 동일)", () => {
    expect(pcm16Dbfs(new Int16Array(0))).toBe(-120);
  });
  it("풀스케일 사인파는 약 -3dBFS", () => {
    const n = 320;
    const s = new Int16Array(n);
    for (let i = 0; i < n; i++) s[i] = Math.round(32767 * Math.sin((2 * Math.PI * i * 8) / n));
    const db = pcm16Dbfs(s);
    expect(db).toBeGreaterThan(-3.5);
    expect(db).toBeLessThan(-2.5);
  });
});

describe("PcmFramer", () => {
  it("320샘플 단위로 640바이트 리틀엔디언 청크를 방출한다", () => {
    const framer = new PcmFramer();
    // 100 + 250 = 350 샘플 → 청크 1개(320) + 잔여 30
    expect(framer.push(new Float32Array(100).fill(0.5))).toEqual([]);
    const chunks = framer.push(new Float32Array(250).fill(0.5));
    expect(chunks.length).toBe(1);
    expect(chunks[0].byteLength).toBe(CHUNK_BYTES);
    // 리틀엔디언 검증: 0.5 → 16384 = 0x4000 → LE 바이트 [0x00, 0x40]
    expect(chunks[0][0]).toBe(0x00);
    expect(chunks[0][1]).toBe(0x40);
  });
  it("여러 청크를 한 번에 방출한다", () => {
    const framer = new PcmFramer();
    const chunks = framer.push(new Float32Array(CHUNK_SAMPLES * 3).fill(0.1));
    expect(chunks.length).toBe(3);
  });
});
// === ANCHOR: PCM_TEST_END ===
```

- [ ] **Step 3: 실패 확인**

```bash
cd apps/web && pnpm test
```

Expected: FAIL — `./pcm` 모듈 없음.

- [ ] **Step 4: 구현** — `apps/web/src/capture/pcm.ts`

```typescript
// === ANCHOR: CAPTURE_PCM_START ===
// 사이드카(apps/client_sidecar)의 브라우저 등가물: 서버 wire 포맷은
// 16kHz mono pcm_s16le 고정, 청크는 20ms=320샘플=640바이트 관례.
export const TARGET_SAMPLE_RATE = 16000;
export const CHUNK_SAMPLES = 320;
export const CHUNK_BYTES = CHUNK_SAMPLES * 2;

export function floatTo16le(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    out[i] = Math.round(clamped * 32767);
  }
  return out;
}

// apps/client_sidecar/audio/rms.py의 pcm16_dbfs 포팅 (빈 입력 -120, epsilon 1e-12).
export function pcm16Dbfs(samples: Int16Array): number {
  if (samples.length === 0) return -120;
  let sumSquares = 0;
  for (let i = 0; i < samples.length; i++) {
    const v = samples[i] / 32768;
    sumSquares += v * v;
  }
  const rms = Math.sqrt(sumSquares / samples.length);
  return 20 * Math.log10(rms + 1e-12);
}

export class PcmFramer {
  private pending: number[] = [];

  push(block: Float32Array): Uint8Array[] {
    const converted = floatTo16le(block);
    for (let i = 0; i < converted.length; i++) this.pending.push(converted[i]);
    const chunks: Uint8Array[] = [];
    while (this.pending.length >= CHUNK_SAMPLES) {
      const frame = this.pending.splice(0, CHUNK_SAMPLES);
      const bytes = new Uint8Array(CHUNK_BYTES);
      const view = new DataView(bytes.buffer);
      for (let i = 0; i < CHUNK_SAMPLES; i++) view.setInt16(i * 2, frame[i], true);
      chunks.push(bytes);
    }
    return chunks;
  }
}
// === ANCHOR: CAPTURE_PCM_END ===
```

- [ ] **Step 5: 통과 확인**

```bash
cd apps/web && pnpm test
```

Expected: PASS (테스트 5개).

- [ ] **Step 6: 커밋**

```bash
git add apps/web/package.json pnpm-lock.yaml apps/web/src/capture/pcm.ts apps/web/src/capture/pcm.test.ts
git commit -m "feat(web): 캡처 PCM 모듈 + vitest 인프라 — s16le 변환·640B 프레이밍·dBFS

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: captureApi — REST·WS URL·자격 증명 저장소

**Files:**
- Create: `apps/web/src/capture/captureApi.ts`
- Test: `apps/web/src/capture/captureApi.test.ts`

**Interfaces:**
- Consumes: 없음 (서버 REST 계약: `apps/desktop/src/console/sessionApi.ts` 참조 원형)
- Produces (Task 6·7이 사용):
  - `loginOperator(email: string, password: string): Promise<{ access_token: string; refresh_token: string }>`
  - `selfEnrollDevice(operatorToken: string, name: string): Promise<string>` — api_key 반환
  - `createCaptureSession(operatorToken: string, title: string): Promise<{ session_id: string; viewer_url: string }>`
  - `endCaptureSession(operatorToken: string, sessionId: string): Promise<void>`
  - `fetchOperatorBackfill(operatorToken: string, sessionId: string): Promise<{ utterances: UtteranceTranscribed[]; session_status: string }>`
  - `operatorWsUrl(sessionId, operatorToken, loc?): string` / `sidecarWsUrl(deviceKey, sessionId, loc?): string`
  - `credentialStore(storage?): { loadDeviceKey(): string | null; saveDeviceKey(k): void; clearDeviceKey(): void }`

- [ ] **Step 1: 실패하는 테스트 작성** — `apps/web/src/capture/captureApi.test.ts`

```typescript
// === ANCHOR: CAPTURE_API_TEST_START ===
import { describe, expect, it } from "vitest";
import { credentialStore, operatorWsUrl, sidecarWsUrl } from "./captureApi";

const loc = { protocol: "https:", host: "example.trycloudflare.com" };

describe("sidecarWsUrl", () => {
  it("https면 wss, 쿼리에 key/session", () => {
    const url = new URL(sidecarWsUrl("dev-key", "sess-1", loc));
    expect(url.protocol).toBe("wss:");
    expect(url.pathname).toBe("/ws/sidecar");
    expect(url.searchParams.get("key")).toBe("dev-key");
    expect(url.searchParams.get("session")).toBe("sess-1");
  });
  it("http(LAN dev)면 ws", () => {
    expect(sidecarWsUrl("k", "s", { protocol: "http:", host: "localhost:5173" })).toMatch(/^ws:\/\/localhost:5173/);
  });
});

describe("operatorWsUrl", () => {
  it("session/access 쿼리", () => {
    const url = new URL(operatorWsUrl("sess-1", "tok", loc));
    expect(url.pathname).toBe("/ws/operator");
    expect(url.searchParams.get("session")).toBe("sess-1");
    expect(url.searchParams.get("access")).toBe("tok");
  });
});

describe("credentialStore", () => {
  it("디바이스 키 저장/조회/삭제", () => {
    const backing = new Map<string, string>();
    const fake = {
      getItem: (k: string) => backing.get(k) ?? null,
      setItem: (k: string, v: string) => void backing.set(k, v),
      removeItem: (k: string) => void backing.delete(k),
    } as Storage;
    const store = credentialStore(fake);
    expect(store.loadDeviceKey()).toBeNull();
    store.saveDeviceKey("abc");
    expect(store.loadDeviceKey()).toBe("abc");
    store.clearDeviceKey();
    expect(store.loadDeviceKey()).toBeNull();
  });
});
// === ANCHOR: CAPTURE_API_TEST_END ===
```

- [ ] **Step 2: 실패 확인** — `cd apps/web && pnpm test` → FAIL (`./captureApi` 없음)

- [ ] **Step 3: 구현** — `apps/web/src/capture/captureApi.ts`

```typescript
// === ANCHOR: CAPTURE_API_START ===
// 데스크탑 콘솔 sessionApi.ts의 웹 등가물. 프로덕션에선 서버가 이 SPA를 직접
// 서빙하므로 상대 경로(fetch "/api/...")가 그대로 서버에 닿는다. dev(5173)는
// vite proxy가 /api·/ws를 localhost:8000으로 넘긴다.
import type { UtteranceTranscribed } from "../types/events";

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

type WsLocation = { protocol: string; host: string };

function wsBase(loc: WsLocation): string {
  const override = (import.meta.env.VITE_WS_BASE ?? "").replace(/\/$/, "");
  if (override) return override;
  const proto = loc.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${loc.host}`;
}

export function sidecarWsUrl(deviceKey: string, sessionId: string, loc: WsLocation = window.location): string {
  const url = new URL(`${wsBase(loc)}/ws/sidecar`);
  url.searchParams.set("key", deviceKey);
  url.searchParams.set("session", sessionId);
  return url.toString();
}

export function operatorWsUrl(sessionId: string, operatorToken: string, loc: WsLocation = window.location): string {
  const url = new URL(`${wsBase(loc)}/ws/operator`);
  url.searchParams.set("session", sessionId);
  url.searchParams.set("access", operatorToken);
  return url.toString();
}

async function parseJson<T>(response: Response, action: string): Promise<T> {
  if (!response.ok) throw new Error(`${action} failed: HTTP ${response.status}`);
  return (await response.json()) as T;
}

function authHeaders(operatorToken: string): HeadersInit {
  return { Authorization: `Bearer ${operatorToken}`, "Content-Type": "application/json" };
}

export async function loginOperator(email: string, password: string): Promise<{ access_token: string; refresh_token: string }> {
  const response = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return parseJson(response, "Login");
}

export async function selfEnrollDevice(operatorToken: string, name: string): Promise<string> {
  const response = await fetch(`${API_BASE}/api/v1/devices/self-enroll`, {
    method: "POST",
    headers: authHeaders(operatorToken),
    body: JSON.stringify({ name }),
  });
  const body = await parseJson<{ id: number; name: string; api_key: string }>(response, "Self-enroll device");
  return body.api_key;
}

export async function createCaptureSession(operatorToken: string, title: string): Promise<{ session_id: string; viewer_url: string }> {
  const response = await fetch(`${API_BASE}/api/v1/sessions`, {
    method: "POST",
    headers: authHeaders(operatorToken),
    body: JSON.stringify({ title, client_label: "web-capture", visibility: "org" }),
  });
  return parseJson(response, "Create session");
}

export async function endCaptureSession(operatorToken: string, sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}/end`, {
    method: "POST",
    headers: authHeaders(operatorToken),
  });
  if (!response.ok) throw new Error(`End session failed: HTTP ${response.status}`);
}

export async function fetchOperatorBackfill(
  operatorToken: string,
  sessionId: string,
): Promise<{ utterances: UtteranceTranscribed[]; session_status: string }> {
  const url = new URL(`${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}/utterances`, window.location.origin);
  url.searchParams.set("limit", "50");
  const response = await fetch(url.toString(), { headers: { Authorization: `Bearer ${operatorToken}` } });
  const body = await parseJson<{ utterances: UtteranceTranscribed[]; session_status?: string }>(response, "Fetch subtitles");
  return { utterances: body.utterances ?? [], session_status: body.session_status ?? "live" };
}

const DEVICE_KEY_STORAGE = "yeson.capture.deviceKey";

export function credentialStore(storage: Storage = window.localStorage) {
  return {
    loadDeviceKey: (): string | null => storage.getItem(DEVICE_KEY_STORAGE),
    saveDeviceKey: (key: string): void => storage.setItem(DEVICE_KEY_STORAGE, key),
    clearDeviceKey: (): void => storage.removeItem(DEVICE_KEY_STORAGE),
  };
}
// === ANCHOR: CAPTURE_API_END ===
```

- [ ] **Step 4: 통과 확인** — `cd apps/web && pnpm test` → PASS

- [ ] **Step 5: 커밋**

```bash
git add apps/web/src/capture/captureApi.ts apps/web/src/capture/captureApi.test.ts
git commit -m "feat(web): 캡처 REST·WS URL·디바이스키 저장소 (기존 서버 API 재사용)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: captureSupport — 브라우저 캐퍼빌리티 감지

**Files:**
- Create: `apps/web/src/capture/captureSupport.ts`
- Test: `apps/web/src/capture/captureSupport.test.ts`

**Interfaces:**
- Produces (Task 7이 사용): `checkCaptureSupport(env?): { ok: true } | { ok: false; reason: "insecure-context" | "no-display-media" }`, `isChromiumLike(ua?): boolean`

- [ ] **Step 1: 실패하는 테스트 작성** — `apps/web/src/capture/captureSupport.test.ts`

```typescript
// === ANCHOR: CAPTURE_SUPPORT_TEST_START ===
import { describe, expect, it } from "vitest";
import { checkCaptureSupport, isChromiumLike } from "./captureSupport";

describe("checkCaptureSupport", () => {
  it("정상", () => {
    expect(checkCaptureSupport({ isSecureContext: true, hasGetDisplayMedia: true })).toEqual({ ok: true });
  });
  it("비보안 컨텍스트 우선 보고", () => {
    expect(checkCaptureSupport({ isSecureContext: false, hasGetDisplayMedia: false })).toEqual({ ok: false, reason: "insecure-context" });
  });
  it("getDisplayMedia 없음", () => {
    expect(checkCaptureSupport({ isSecureContext: true, hasGetDisplayMedia: false })).toEqual({ ok: false, reason: "no-display-media" });
  });
});

describe("isChromiumLike", () => {
  it("Chrome/Edge true", () => {
    expect(isChromiumLike("Mozilla/5.0 ... Chrome/126.0 Safari/537.36")).toBe(true);
    expect(isChromiumLike("Mozilla/5.0 ... Chrome/126.0 Safari/537.36 Edg/126.0")).toBe(true);
  });
  it("Firefox/Safari false", () => {
    expect(isChromiumLike("Mozilla/5.0 (Macintosh) Gecko/20100101 Firefox/128.0")).toBe(false);
    expect(isChromiumLike("Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15")).toBe(false);
  });
});
// === ANCHOR: CAPTURE_SUPPORT_TEST_END ===
```

- [ ] **Step 2: 실패 확인** — `cd apps/web && pnpm test` → FAIL

- [ ] **Step 3: 구현** — `apps/web/src/capture/captureSupport.ts`

```typescript
// === ANCHOR: CAPTURE_SUPPORT_START ===
// 탭 오디오 캡처는 Chromium 계열 + 보안 컨텍스트(https 또는 localhost)에서만
// 동작한다. LAN http://<IP>:8000 접속이 가장 흔한 실패 경로라 먼저 검사한다.
export type CaptureSupport = { ok: true } | { ok: false; reason: "insecure-context" | "no-display-media" };

type SupportEnv = { isSecureContext: boolean; hasGetDisplayMedia: boolean };

function defaultEnv(): SupportEnv {
  return {
    isSecureContext: window.isSecureContext,
    hasGetDisplayMedia: typeof navigator.mediaDevices?.getDisplayMedia === "function",
  };
}

export function checkCaptureSupport(env: SupportEnv = defaultEnv()): CaptureSupport {
  if (!env.isSecureContext) return { ok: false, reason: "insecure-context" };
  if (!env.hasGetDisplayMedia) return { ok: false, reason: "no-display-media" };
  return { ok: true };
}

export function isChromiumLike(ua: string = navigator.userAgent): boolean {
  return /Chrom(e|ium)\//.test(ua);
}
// === ANCHOR: CAPTURE_SUPPORT_END ===
```

- [ ] **Step 4: 통과 확인** — `cd apps/web && pnpm test` → PASS

- [ ] **Step 5: 커밋**

```bash
git add apps/web/src/capture/captureSupport.ts apps/web/src/capture/captureSupport.test.ts
git commit -m "feat(web): 캡처 지원 여부 감지(보안 컨텍스트·getDisplayMedia·Chromium)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: audioWsClient — /ws/sidecar 프로토콜 클라이언트

**Files:**
- Create: `apps/web/src/capture/audioWsClient.ts`
- Test: `apps/web/src/capture/audioWsClient.test.ts`

**Interfaces:**
- Consumes: `sidecarWsUrl`(Task 2)이 만든 URL 문자열
- Produces (Task 6이 사용):
  - `type AudioWsStatus = "idle" | "connecting" | "streaming" | "reconnecting" | "rejected" | "stopped"`
  - `class AudioWsClient { constructor(url: string, onStatus: (s: AudioWsStatus) => void, wsFactory?: (url: string) => WebSocketLike); start(): void; sendChunk(chunk: Uint8Array): void; stop(reason: string | null): void; }`
  - `type WebSocketLike = Pick<WebSocket, "send" | "close" | "readyState"> & { onopen/onclose/onerror 할당 가능 }`

프로토콜 규칙(사이드카 `apps/client_sidecar/transport/audio_ws.py`와 동일 계약):
- open 직후 `{"type":"audio.started","sample_rate":16000,"channels":1,"format":"pcm_s16le","started_at":<ISO8601>}` 를 **첫 메시지**로 전송
- 바이너리 청크 50개마다 `{"type":"chunk_meta","seq":<누적 청크 수>,"started_at":<ISO>}`
- `stop(reason)` 시 `{"type":"audio.stopped","reason":<reason>}` 전송 후 close
- 미연결 상태의 `sendChunk`는 버리고 드롭 카운트만 증가(서버도 `audio.started` 이전 오디오는 버림)
- 재접속: 지수 백오프 1s→2s→…→30s(사이드카와 동일), 재접속 open 시 `audio.started` 재전송
- **거부 감지**: 서버는 인증 실패·세션 종료·타 디바이스 점유·시간 초과 시 reason 없이 1008로 닫는다. open 후 2초 안에 닫히는 일이 3회 연속되면 status `"rejected"`로 전환하고 재시도를 멈춘다

- [ ] **Step 1: 실패하는 테스트 작성** — `apps/web/src/capture/audioWsClient.test.ts`

```typescript
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
```

- [ ] **Step 2: 실패 확인** — `cd apps/web && pnpm test` → FAIL

- [ ] **Step 3: 구현** — `apps/web/src/capture/audioWsClient.ts`

```typescript
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
      if (openMs > 0 && openMs < REJECT_WINDOW_MS) {
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
```

- [ ] **Step 4: 통과 확인** — `cd apps/web && pnpm test` → PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add apps/web/src/capture/audioWsClient.ts apps/web/src/capture/audioWsClient.test.ts
git commit -m "feat(web): /ws/sidecar 프로토콜 클라이언트 — 전송순서·chunk_meta·백오프·거부감지

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: tabCapture — 탭 오디오 + 마이크 믹싱 파이프라인

**Files:**
- Create: `apps/web/src/capture/tabCapture.ts`

**Interfaces:**
- Consumes: 없음 (브라우저 API만)
- Produces (Task 6이 사용):
  - `class NoTabAudioError extends Error` — 탭 선택 시 "오디오 공유" 체크 누락
  - `startTabCapture(opts: { onPcmBlock: (block: Float32Array) => void; onEnded: () => void }): Promise<TabCaptureHandle>`
  - `type TabCaptureHandle = { attachMic(): Promise<void>; detachMic(): void; micAttached(): boolean; stop(): void }`
- `onPcmBlock`은 16kHz mono Float32 블록(WebAudio 렌더 퀀텀=128샘플)을 전달한다. 프레이밍·변환은 호출자(Task 6)가 `PcmFramer`로 수행.

브라우저 API 의존이라 단위 테스트 대상에서 제외(순수 로직은 Task 1·4에 이미 격리됨). 검증은 Task 8의 dev E2E.

- [ ] **Step 1: 구현** — `apps/web/src/capture/tabCapture.ts`

```typescript
// === ANCHOR: TAB_CAPTURE_START ===
// getDisplayMedia(탭+오디오) → AudioContext(16kHz, 브라우저 내장 리샘플) →
// AudioWorklet 탭에서 Float32 블록을 메인 스레드로 전달. 마이크는 동일 워크릿
// 입력에 추가 연결(Web Audio는 다중 연결을 자동 합산)으로 믹싱한다.
// destination에 연결하지 않으므로 로컬 재생에는 영향 없음.
import { TARGET_SAMPLE_RATE } from "./pcm";

export class NoTabAudioError extends Error {
  constructor() {
    super("selected surface has no audio track");
    this.name = "NoTabAudioError";
  }
}

export type TabCaptureHandle = {
  attachMic(): Promise<void>;
  detachMic(): void;
  micAttached(): boolean;
  stop(): void;
};

const WORKLET_SOURCE = `
class PcmTapProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length > 0) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("pcm-tap", PcmTapProcessor);
`;

export async function startTabCapture(opts: {
  onPcmBlock: (block: Float32Array) => void;
  onEnded: () => void;
}): Promise<TabCaptureHandle> {
  const display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
  const audioTracks = display.getAudioTracks();
  if (audioTracks.length === 0) {
    display.getTracks().forEach((t) => t.stop());
    throw new NoTabAudioError();
  }

  const ctx = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
  const workletUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "text/javascript" }));
  try {
    await ctx.audioWorklet.addModule(workletUrl);
  } finally {
    URL.revokeObjectURL(workletUrl);
  }
  const tap = new AudioWorkletNode(ctx, "pcm-tap", {
    numberOfInputs: 1,
    numberOfOutputs: 0,
    channelCount: 1,
    channelCountMode: "explicit",
    channelInterpretation: "speakers",
  });
  tap.port.onmessage = (event: MessageEvent<Float32Array>) => opts.onPcmBlock(event.data);

  const tabSource = ctx.createMediaStreamSource(new MediaStream(audioTracks));
  tabSource.connect(tap);

  let stopped = false;
  let micStream: MediaStream | null = null;
  let micSource: MediaStreamAudioSourceNode | null = null;

  function handleEnded() {
    if (stopped) return;
    stop();
    opts.onEnded();
  }
  display.getTracks().forEach((track) => track.addEventListener("ended", handleEnded));

  function detachMic() {
    micSource?.disconnect();
    micSource = null;
    micStream?.getTracks().forEach((t) => t.stop());
    micStream = null;
  }

  function stop() {
    if (stopped) return;
    stopped = true;
    detachMic();
    tabSource.disconnect();
    tap.port.onmessage = null;
    display.getTracks().forEach((t) => t.stop());
    void ctx.close();
  }

  return {
    async attachMic() {
      if (stopped || micSource) return;
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
      micSource = ctx.createMediaStreamSource(micStream);
      micSource.connect(tap);
    },
    detachMic,
    micAttached: () => micSource !== null,
    stop,
  };
}
// === ANCHOR: TAB_CAPTURE_END ===
```

- [ ] **Step 2: 타입 검사**

```bash
cd apps/web && pnpm exec tsc --noEmit
```

Expected: 오류 없음.

- [ ] **Step 3: 커밋**

```bash
git add apps/web/src/capture/tabCapture.ts
git commit -m "feat(web): 탭 오디오 캡처 파이프라인 — AudioWorklet 16kHz 탭 + 마이크 믹싱 옵션

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: useOperatorSubtitles + useCaptureSession 훅

**Files:**
- Create: `apps/web/src/capture/useOperatorSubtitles.ts`
- Create: `apps/web/src/capture/useCaptureSession.ts`

**Interfaces:**
- Consumes: Task 1 `PcmFramer`/`pcm16Dbfs`, Task 2 전체, Task 4 `AudioWsClient`, Task 5 `startTabCapture`/`NoTabAudioError`, 기존 `apps/web/src/lib/utterances.ts`의 `upsertUtterance`/`latestUtterance`, `apps/web/src/types/events.ts`
- Produces (Task 7이 사용):
  - `useOperatorSubtitles(sessionId: string | null, operatorToken: string | null): { utterances: UtteranceTranscribed[]; latest: UtteranceTranscribed | null; connected: boolean }`
  - `useCaptureSession(): CaptureSessionState & CaptureSessionActions` (아래 코드의 타입 그대로)

- [ ] **Step 1: useOperatorSubtitles 구현** — `apps/web/src/capture/useOperatorSubtitles.ts`

`useViewerWS`(apps/web/src/hooks/useViewerWS.ts)와 같은 패턴, 단 운영자 엔드포인트 사용. `ai.status` 등 미지의 이벤트 타입은 무시한다.

```typescript
// === ANCHOR: USE_OPERATOR_SUBTITLES_START ===
import { useEffect, useRef, useState } from "react";
import type { UtteranceTranscribed } from "../types/events";
import { latestUtterance, upsertUtterance } from "../lib/utterances";
import { fetchOperatorBackfill, operatorWsUrl } from "./captureApi";

export type OperatorSubtitles = {
  utterances: UtteranceTranscribed[];
  latest: UtteranceTranscribed | null;
  connected: boolean;
};

export function useOperatorSubtitles(sessionId: string | null, operatorToken: string | null): OperatorSubtitles {
  const [state, setState] = useState<OperatorSubtitles>({ utterances: [], latest: null, connected: false });
  const lastSeqRef = useRef(0);

  useEffect(() => {
    if (!sessionId || !operatorToken) {
      setState({ utterances: [], latest: null, connected: false });
      return;
    }
    lastSeqRef.current = 0;
    let active = true;
    let ws: WebSocket | null = null;
    let backoff = 1000;
    let ended = false;

    async function start() {
      try {
        const backfill = await fetchOperatorBackfill(operatorToken!, sessionId!);
        if (!active) return;
        const sorted = [...backfill.utterances].sort((a, b) => a.seq - b.seq).reduce<UtteranceTranscribed[]>(upsertUtterance, []);
        const last = latestUtterance(sorted);
        if (last) lastSeqRef.current = last.seq;
        setState((s) => ({ ...s, utterances: sorted, latest: last }));
        if (backfill.session_status === "ended") {
          ended = true;
          return;
        }
      } catch {}
      connect();
    }

    function connect() {
      if (!active) return;
      ws = new WebSocket(operatorWsUrl(sessionId!, operatorToken!));
      ws.onopen = () => {
        backoff = 1000;
        setState((s) => ({ ...s, connected: true }));
      };
      ws.onmessage = (e) => {
        try {
          const evt = JSON.parse(e.data) as { type: string } & UtteranceTranscribed;
          if (evt.type === "session.ended") {
            ended = true;
            setState((s) => ({ ...s, connected: false }));
            ws?.close();
            return;
          }
          if (evt.type !== "utterance.transcribed") return;
          if (evt.seq < lastSeqRef.current) return;
          lastSeqRef.current = Math.max(lastSeqRef.current, evt.seq);
          setState((s) => {
            const utterances = upsertUtterance(s.utterances, evt);
            return { ...s, utterances, latest: latestUtterance(utterances) };
          });
        } catch {}
      };
      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        if (!active || ended) return;
        setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 30000);
      };
    }

    start();
    return () => {
      active = false;
      ws?.close();
    };
  }, [sessionId, operatorToken]);

  return state;
}
// === ANCHOR: USE_OPERATOR_SUBTITLES_END ===
```

- [ ] **Step 2: useCaptureSession 구현** — `apps/web/src/capture/useCaptureSession.ts`

```typescript
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
```

- [ ] **Step 3: 타입 검사 + 기존 테스트 회귀 확인**

```bash
cd apps/web && pnpm exec tsc --noEmit && pnpm test
```

Expected: 타입 오류 없음, 기존 테스트 전부 PASS.

- [ ] **Step 4: 커밋**

```bash
git add apps/web/src/capture/useOperatorSubtitles.ts apps/web/src/capture/useCaptureSession.ts
git commit -m "feat(web): 캡처 세션 오케스트레이션 훅 + 운영자 자막 미리보기 훅

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: CaptureView UI + 라우팅 + QR

**Files:**
- Modify: `apps/web/package.json` (qrcode 의존성)
- Create: `apps/web/src/capture/CaptureView.tsx`
- Modify: `apps/web/src/App.tsx` (라우트 분기 — 앵커 `APP_START`~`APP_END` 내부만 수정)

**Interfaces:**
- Consumes: Task 3 `checkCaptureSupport`/`isChromiumLike`, Task 6 두 훅
- Produces: `<CaptureView />` (default 아님, named export)

- [ ] **Step 1: qrcode 의존성 추가**

`apps/web/package.json` `dependencies`에 `"qrcode": "^1.5.4"`, `devDependencies`에 `"@types/qrcode": "^1.5.6"` 추가(버전은 `apps/desktop/package.json`과 동일) 후:

```bash
cd /Users/usabatch/coding/yeson_dev/yeson_meet && pnpm install
```

- [ ] **Step 2: CaptureView 구현** — `apps/web/src/capture/CaptureView.tsx`

```tsx
// === ANCHOR: CAPTURE_VIEW_START ===
import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { checkCaptureSupport, isChromiumLike } from "./captureSupport";
import { useCaptureSession } from "./useCaptureSession";
import { useOperatorSubtitles } from "./useOperatorSubtitles";

function SupportBanners() {
  const support = checkCaptureSupport();
  const chromium = isChromiumLike();
  if (support.ok && chromium) return null;
  return (
    <div className="space-y-2">
      {!support.ok && support.reason === "insecure-context" && (
        <div className="rounded-lg bg-amber-900/60 border border-amber-500 px-4 py-3 text-sm">
          이 주소에서는 탭 캡처를 쓸 수 없습니다. <b>https 주소(공유용 터널 링크)</b> 또는 localhost로 접속하세요.
        </div>
      )}
      {!support.ok && support.reason === "no-display-media" && (
        <div className="rounded-lg bg-amber-900/60 border border-amber-500 px-4 py-3 text-sm">
          이 브라우저는 탭 캡처를 지원하지 않습니다. <b>Chrome 또는 Edge</b>로 접속하세요.
        </div>
      )}
      {support.ok && !chromium && (
        <div className="rounded-lg bg-amber-900/60 border border-amber-500 px-4 py-3 text-sm">
          탭 오디오 캡처는 Chrome/Edge 계열에서만 안정적으로 동작합니다. 문제가 생기면 Chrome으로 접속하세요.
        </div>
      )}
    </div>
  );
}

function ViewerQr({ viewerUrl }: { viewerUrl: string }) {
  const [qrSvg, setQrSvg] = useState("");
  useEffect(() => {
    let active = true;
    QRCode.toString(viewerUrl, {
      type: "svg",
      errorCorrectionLevel: "M",
      margin: 2,
      width: 140,
      color: { dark: "#020617", light: "#f8fafc" },
    }).then((svg) => {
      if (active) setQrSvg(svg);
    });
    return () => {
      active = false;
    };
  }, [viewerUrl]);
  return (
    <div className="flex items-center gap-4">
      <div className="rounded-lg bg-slate-50 p-2" dangerouslySetInnerHTML={{ __html: qrSvg }} />
      <div className="text-sm space-y-2">
        <p className="text-slate-300">참석자 자막 링크</p>
        <p className="break-all text-slate-100">{viewerUrl}</p>
        <button
          className="rounded bg-slate-700 px-3 py-1 hover:bg-slate-600"
          onClick={() => void navigator.clipboard.writeText(viewerUrl)}
        >
          링크 복사
        </button>
      </div>
    </div>
  );
}

function LevelMeter({ dbfs }: { dbfs: number }) {
  const pct = Math.round(Math.min(100, Math.max(0, ((dbfs + 60) / 60) * 100)));
  const quiet = dbfs < -50;
  return (
    <div>
      <div className="h-2 w-full rounded bg-slate-700">
        <div className={`h-2 rounded ${quiet ? "bg-amber-500" : "bg-emerald-500"}`} style={{ width: `${pct}%` }} />
      </div>
      {quiet && <p className="mt-1 text-xs text-amber-400">오디오가 거의 들어오지 않습니다 — 탭 선택 시 '탭 오디오 공유' 체크를 확인하세요.</p>}
    </div>
  );
}

export function CaptureView() {
  const s = useCaptureSession();
  const subtitles = useOperatorSubtitles(s.phase === "capturing" ? s.sessionId : null, s.operatorToken);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const recent = subtitles.utterances.filter((u) => u.is_final).slice(-3);

  return (
    <main className="min-h-screen bg-slate-900 text-slate-100 flex justify-center p-6">
      <div className="w-full max-w-2xl space-y-6">
        <header>
          <h1 className="text-2xl font-bold">웹 캡처 — 실시간 자막</h1>
          <p className="text-sm text-slate-400">구글밋 탭의 소리를 잡아 자막을 만듭니다. 앱 설치가 필요 없습니다.</p>
        </header>
        <SupportBanners />
        {s.error && (
          <div className="rounded-lg bg-rose-900/60 border border-rose-500 px-4 py-3 text-sm flex justify-between gap-4">
            <span>{s.error}</span>
            <button className="shrink-0 underline" onClick={s.resetError}>닫기</button>
          </div>
        )}

        {s.phase === "login" && (
          <form
            className="space-y-3 rounded-xl bg-slate-800 p-5"
            onSubmit={(e) => {
              e.preventDefault();
              void s.login(email, password);
            }}
          >
            <h2 className="font-semibold">운영자 로그인</h2>
            <input className="w-full rounded bg-slate-700 px-3 py-2" type="email" placeholder="이메일" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <input className="w-full rounded bg-slate-700 px-3 py-2" type="password" placeholder="비밀번호" value={password} onChange={(e) => setPassword(e.target.value)} required />
            <button className="w-full rounded bg-emerald-600 py-2 font-semibold hover:bg-emerald-500 disabled:opacity-50" disabled={s.busy}>
              {s.busy ? "로그인 중…" : "로그인"}
            </button>
          </form>
        )}

        {s.phase === "ready" && (
          <div className="space-y-3 rounded-xl bg-slate-800 p-5">
            <h2 className="font-semibold">회의 시작</h2>
            <input className="w-full rounded bg-slate-700 px-3 py-2" placeholder="회의 제목" value={s.title} onChange={(e) => s.setTitle(e.target.value)} />
            {!s.sessionId ? (
              <button className="w-full rounded bg-emerald-600 py-2 font-semibold hover:bg-emerald-500 disabled:opacity-50" disabled={s.busy || !s.title.trim()} onClick={() => void s.startMeeting()}>
                {s.busy ? "생성 중…" : "회의 만들기"}
              </button>
            ) : (
              <>
                {s.viewerUrl && <ViewerQr viewerUrl={s.viewerUrl} />}
                <div className="rounded-lg bg-slate-900/60 px-4 py-3 text-sm text-slate-300">
                  다음 화면에서 <b>구글밋이 열린 탭</b>을 선택하고 왼쪽 아래 <b>'탭 오디오 공유'를 반드시 체크</b>하세요.
                </div>
                <button className="w-full rounded bg-emerald-600 py-2 font-semibold hover:bg-emerald-500 disabled:opacity-50" disabled={s.busy} onClick={() => void s.startCapture()}>
                  {s.busy ? "준비 중…" : "탭 선택하고 캡처 시작"}
                </button>
              </>
            )}
          </div>
        )}

        {s.phase === "capturing" && (
          <div className="space-y-4 rounded-xl bg-slate-800 p-5">
            <div className="flex items-center justify-between">
              <h2 className="font-semibold">캡처 중 — {s.title}</h2>
              <span className={`text-xs rounded-full px-2 py-1 ${s.wsStatus === "streaming" ? "bg-emerald-700" : "bg-amber-700"}`}>
                {s.wsStatus === "streaming" ? "전송 중" : s.wsStatus === "reconnecting" ? "재접속 중" : s.wsStatus}
              </span>
            </div>
            {s.captureLost && (
              <div className="rounded-lg bg-amber-900/60 border border-amber-500 px-4 py-3 text-sm">
                캡처가 끊겼습니다(공유 중지/탭 닫힘). 회의는 유지 중 —
                <button className="ml-2 underline" onClick={() => void s.startCapture()}>다시 탭 선택</button>
              </div>
            )}
            <LevelMeter dbfs={s.levelDbfs} />
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={s.micOn} onChange={() => void s.toggleMic()} />
              내 목소리 포함(마이크)
            </label>
            <div className="rounded-lg bg-slate-900/60 p-3 min-h-[5rem] space-y-1">
              {recent.length === 0 && <p className="text-sm text-slate-500">자막이 오면 여기 표시됩니다…</p>}
              {recent.map((u) => (
                <p key={u.seq} className="text-sm">
                  <span className="text-slate-400">{u.text_en}</span> <span className="text-slate-100">{u.text_ko}</span>
                </p>
              ))}
            </div>
            {s.viewerUrl && <ViewerQr viewerUrl={s.viewerUrl} />}
            <button className="w-full rounded bg-rose-700 py-2 font-semibold hover:bg-rose-600 disabled:opacity-50" disabled={s.busy} onClick={() => void s.stopCaptureAndEnd()}>
              캡처 중지 + 회의 종료
            </button>
          </div>
        )}

        {s.phase === "ended" && (
          <div className="rounded-xl bg-slate-800 p-5 space-y-3">
            <h2 className="font-semibold">회의가 종료됐습니다</h2>
            <p className="text-sm text-slate-400">보고서·요약은 서버 콘솔(또는 데스크탑 앱)에서 확인하세요.</p>
            <button className="rounded bg-slate-700 px-4 py-2 hover:bg-slate-600" onClick={() => window.location.reload()}>
              새 회의 시작
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
// === ANCHOR: CAPTURE_VIEW_END ===
```

- [ ] **Step 3: 라우팅 추가** — `apps/web/src/App.tsx` 수정. `parseViewerToken` 위(import 아래)에 분기 추가:

기존:

```tsx
export default function App() {
  const token = parseViewerToken(); // vibelign: allow-secret
  if (token) return <SubtitleView token={token} />;
```

변경(import에 `import { CaptureView } from "./capture/CaptureView";` 추가 후):

```tsx
export default function App() {
  if (window.location.pathname.replace(/\/$/, "") === "/capture") return <CaptureView />;

  const token = parseViewerToken(); // vibelign: allow-secret
  if (token) return <SubtitleView token={token} />;
```

- [ ] **Step 4: 빌드 + 테스트**

```bash
cd apps/web && pnpm exec tsc --noEmit && pnpm test && pnpm build
```

Expected: 전부 통과, `dist/` 생성.

- [ ] **Step 5: 커밋**

```bash
git add apps/web/package.json pnpm-lock.yaml apps/web/src/capture/CaptureView.tsx apps/web/src/App.tsx
git commit -m "feat(web): /capture 페이지 — 로그인·회의생성·탭캡처·레벨미터·자막미리보기·QR

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: E2E 검증 + 운영 가이드 문서

**Files:**
- Create: `docs/web-capture-operator-guide.md`

**Interfaces:** 없음 (검증 + 문서)

- [ ] **Step 1: dev E2E (localhost는 보안 컨텍스트라 터널 없이 검증 가능)**

1. 서버 기동(개발 모드 또는 서버 콘솔 앱 실행)
2. `cd apps/web && pnpm dev` → Chrome에서 `http://localhost:5173/capture`
3. 다른 탭에서 구글밋 회의(또는 소리 나는 아무 탭, 예: YouTube) 열기
4. 로그인 → 회의 제목 입력 → 회의 만들기 → QR 표시 확인
5. "탭 선택하고 캡처 시작" → 소리 나는 탭 선택 + **탭 오디오 공유 체크**
6. 확인 항목: 상태칩 "전송 중" / 레벨미터 움직임 / 자막 미리보기 도착 / 뷰어 링크(별도 브라우저)에서 자막 표시
7. 오디오 공유 체크 **없이** 탭 선택 → NoTabAudioError 안내 문구 확인
8. Chrome 공유 중지 클릭 → "다시 탭 선택" 배너 확인 → 재선택 시 자막 재개 확인
9. "캡처 중지 + 회의 종료" → 종료 화면, 서버 콘솔에서 세션 ended 확인
10. 마이크 토글 ON → 말해서 자막 생성 확인(에코캔슬 동작 포함)

- [ ] **Step 2: 프로덕션 서빙 검증** — `pnpm build` 산출물을 서버가 서빙하는지: `YESON_WEB_DIST=$(pwd)/apps/web/dist`로 서버를 띄우거나 번들 재동결 후, `http://<서버IP>:8000/capture` 접속 → **비보안 컨텍스트 배너**가 뜨는지 확인(의도된 동작), 터널 https 주소 `/capture`에서 전체 흐름 1회 반복.

주의(메모리 참조): `apps/server` 소스는 안 바꿨으므로 재동결 불필요, 단 웹 dist가 번들에 포함되는 빌드(`build-server.sh`)를 쓰는 배포에선 재동결해야 새 페이지가 실린다.

- [ ] **Step 3: Windows 실기기 E2E (필수 — Mac 검증만으로 완료 처리 금지)**

Windows 실기기에서 Step 1의 4~10번 항목을 반복한다. Windows 전용 확인 포인트:

1. Windows Chrome에서 터널 https 주소 `/capture` 접속 → 전체 흐름(로그인→회의→탭 캡처→자막→종료) 1회
2. Windows **Edge**에서도 동일 흐름 1회 (사내 브라우저 구성이 불확실하므로 두 브라우저 모두)
3. 탭 선택 다이얼로그에서 '탭' 대신 '전체 화면'+'시스템 오디오 공유'를 선택해도 자막이 나오는지 확인 (Windows Chrome은 시스템 오디오 캡처 지원 — 되면 가이드에 대안 경로로 기록, 안 되면 탭 전용으로 기록)
4. 한글 로케일 환경에서 UI 문자열·자막 표시 깨짐 없는지 확인
5. 결과를 릴리스 체크리스트(아래 Step 4 문서)에 기록

- [ ] **Step 4: 운영 가이드 작성** — `docs/web-capture-operator-guide.md`

```markdown
# 웹 캡처 사용 가이드 (진행자용)

## 사용법
1. Chrome/Edge에서 공유받은 서버 주소 뒤에 `/capture`를 붙여 접속 (https 터널 주소 권장)
2. 운영자 계정으로 로그인 → 회의 제목 입력 → 회의 만들기
3. 참석자에게 QR/링크 공유 (자막 뷰어)
4. "탭 선택하고 캡처 시작" → 구글밋이 열린 **탭** 선택 + **'탭 오디오 공유' 체크 필수**
5. 회의가 끝나면 "캡처 중지 + 회의 종료"

## 제약
- Chrome/Edge(Chromium) 전용. Safari/Firefox는 데스크탑 앱을 사용하세요.
- `http://<서버IP>` 직접 접속으로는 캡처가 안 됩니다(브라우저 보안 정책). https 터널 주소 또는 데스크탑 앱을 쓰세요.
- 탭 오디오에는 상대방 목소리만 담깁니다. 내 목소리도 자막으로 만들려면 "내 목소리 포함(마이크)"을 켜세요.
- 캡처 페이지 탭은 회의 중 계속 열어 두세요.
- Zoom/Teams 데스크탑 앱 회의는 웹 캡처가 잡지 못합니다 → 데스크탑 앱 사용.

## 릴리스 체크리스트 추가 항목
- [ ] 오디오 수신 프로토콜(`/ws/sidecar` 계약: audio.started/chunk_meta/audio.stopped, 16kHz mono s16le)을 변경한 경우 **사이드카와 웹 캡처 두 클라이언트를 모두 검증**했다
- [ ] 웹 dist를 번들에 포함하는 배포라면 재동결(build-server.sh) 후 `/capture` 접속 확인
```

- [ ] **Step 5: 커밋**

```bash
git add docs/web-capture-operator-guide.md
git commit -m "docs: 웹 캡처 운영 가이드 + 프로토콜 이중 클라이언트 릴리스 체크 항목

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review 결과 (작성 후 점검)

- **스펙 커버리지**: 라우팅/서빙(T7·T8), 상태 머신·로그인·자가등록·세션(T2·T6·T7), 오디오 파이프라인·마이크 혼합·레벨미터(T1·T5·T6), 프로토콜 계약(T4), 자막 미리보기·QR(T6·T7), 에러 처리 전 항목(T3 배너, T4 거부·백오프, T5 NoTabAudio·ended, T6 매핑, T7 표시), 테스트·E2E·릴리스 체크(전 태스크+T8) — 스펙의 각 섹션에 대응 태스크 있음.
- **스펙과 다른 점(의도된 2건)**: ① 백오프를 스펙의 "2s/10s/30s" 대신 실제 사이드카와 동일한 지수(1s→30s)로 구현 — "사이드카와 동일"이 우선 의도. ② 서버 1008에 reason이 없음이 확인되어 "사유별 메시지" 대신 "거부 감지 + 통합 안내 + 키 재등록" 방식 채택.
- **플레이스홀더 스캔**: TBD/TODO/"적절히" 없음. 모든 코드 스텝에 전체 코드 포함.
- **타입 일관성**: `PcmFramer.push`/`pcm16Dbfs`(T1) ↔ T6 사용부, `AudioWsClient` 생성자·`AudioWsStatus`(T4) ↔ T6, `credentialStore`/URL 빌더(T2) ↔ T6, `TabCaptureHandle`(T5) ↔ T6, 훅 반환(T6) ↔ T7 — 서명 일치 확인.
