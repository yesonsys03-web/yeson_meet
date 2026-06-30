// === ANCHOR: USE_LIVE_SUBTITLE_STREAM_START ===
import { useEffect, useRef, useState } from "react";
import { appLogger } from "../diagnostics/appLog";
import { SubtitleTimingRecorder } from "../timing/subtitleTiming";
import { fetchOperatorBackfill, operatorWsUrl } from "./sessionApi";
import type { DomainEvent, UtteranceTranscribed } from "./types";
import { latestUtterance, upsertUtterance } from "./utterances";

// Subtitle-arrival timing for Phase 0/1 baseline measurements.
// DevTools usage: `copy(window.__yesonTimingExport())` after a scenario,
// paste into docs/baselines/<date>-<scenario>-client.json.
const subtitleTimingRecorder = new SubtitleTimingRecorder();
declare global {
  interface Window {
    __yesonTimingExport?: () => string;
    __yesonTimingReset?: () => void;
  }
}
if (typeof window !== "undefined") {
  window.__yesonTimingExport = () => subtitleTimingRecorder.toJSON();
  window.__yesonTimingReset = () => subtitleTimingRecorder.reset();
}

export type LiveSubtitleState = {
  utterances: UtteranceTranscribed[];
  latest: UtteranceTranscribed | null;
  connected: boolean;
  ended: boolean;
  error: string | null;
  // Set when the server reports a permanent AI-provider failure (e.g. Gemini
  // billing/quota/auth). Cleared once subtitles resume. null = healthy.
  providerError: string | null;
};

const initialState: LiveSubtitleState = {
  utterances: [],
  latest: null,
  connected: false,
  ended: false,
  error: null,
  providerError: null,
};

export function useLiveSubtitleStream(sessionId: string | null, operatorToken: string): LiveSubtitleState {
  const [state, setState] = useState<LiveSubtitleState>(initialState);
  const lastSeqRef = useRef(0);

  useEffect(() => {
    if (!sessionId || !operatorToken) {
      setState(initialState);
      return;
    }
    const activeSessionId = sessionId;
    const activeOperatorToken = operatorToken;

    // New session → reset the seq de-dup cursor. Per-session seq numbers restart
    // low, so a stale cursor left over from a prior meeting (e.g. 101) would
    // reject the new meeting's seq 1..N and show NO subtitles after an end →
    // restart. Backfill below re-raises it if this session already has history.
    lastSeqRef.current = 0;

    let active = true;
    let ws: WebSocket | null = null;
    let backoff = 1000;
    let sessionEnded = false;

    async function start() {
      try {
        const backfillStartedAt = performance.now();
        const backfill = await fetchOperatorBackfill(activeSessionId, activeOperatorToken);
        if (!active) return;
        const sorted = [...backfill.utterances].sort((a, b) => a.seq - b.seq).reduce<UtteranceTranscribed[]>(upsertUtterance, []);
        const last = latestUtterance(sorted);
        if (last) lastSeqRef.current = last.seq;
        setState((current) => ({
          ...current,
          utterances: sorted,
          latest: last,
          ended: backfill.session_status === "ended",
          error: null,
        }));
        appLogger.latency("subtitle", `Backfill loaded ${sorted.length} utterances`, performance.now() - backfillStartedAt, { detail: `session_status=${backfill.session_status}` });
        if (backfill.session_status === "ended") {
          sessionEnded = true;
          return;
        }
      } catch (error) {
        if (!active) return;
        setState((current) => ({ ...current, error: error instanceof Error ? error.message : String(error) }));
      }
      connect();
    }

    function connect() {
      if (!active) return;
      const connectStartedAt = performance.now();
      appLogger.info("subtitle", "Subtitle WebSocket connecting");
      ws = new WebSocket(operatorWsUrl(activeSessionId, activeOperatorToken));
      ws.onopen = () => {
        backoff = 1000;
        appLogger.latency("subtitle", "Subtitle WebSocket connected", performance.now() - connectStartedAt);
        setState((current) => ({ ...current, connected: true, error: null }));
      };
      ws.onmessage = (event) => applyMessage(event.data);
      ws.onerror = () => {
        appLogger.warn("subtitle", "Subtitle WebSocket error");
        setState((current) => ({ ...current, error: "subtitle ws error" }));
      };
      ws.onclose = (event) => {
        appLogger.info("subtitle", "Subtitle WebSocket closed", { detail: `code=${event.code} clean=${event.wasClean}` });
        setState((current) => ({ ...current, connected: false }));
        if (!active || sessionEnded) return;
        window.setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 30000);
      };
    }

    function applyMessage(raw: string) {
      const event = parseDomainEvent(raw);
      if (!event) return;
      if (event.type === "session.ended") {
        sessionEnded = true;
        appLogger.info("subtitle", "Session ended event received");
        setState((current) => ({ ...current, connected: false, ended: true, error: null }));
        ws?.close();
        return;
      }
      if (event.type === "ai.status") {
        const detail = event.detail ?? event.status;
        appLogger.error("subtitle", "AI provider status", { detail });
        setState((current) => ({ ...current, providerError: detail }));
        return;
      }
      if (event.seq < lastSeqRef.current) return;
      lastSeqRef.current = Math.max(lastSeqRef.current, event.seq);
      subtitleTimingRecorder.markArrival({ seq: event.seq, isFinal: event.is_final });
      logEventLatency(event);
      setState((current) => {
        const utterances = upsertUtterance(current.utterances, event);
        // A fresh subtitle means the provider recovered — clear any prior error.
        return { ...current, utterances, latest: latestUtterance(utterances), providerError: null };
      });
    }

    start();
    return () => {
      active = false;
      ws?.close();
    };
  }, [operatorToken, sessionId]);

  return state;
}

function parseDomainEvent(raw: string): DomainEvent | null {
  try {
    const event = JSON.parse(raw) as DomainEvent;
    if (
      event.type === "utterance.transcribed" ||
      event.type === "session.ended" ||
      event.type === "ai.status"
    ) {
      return event;
    }
  } catch (error) {
    appLogger.warn("subtitle", "Ignoring malformed subtitle event", { detail: error instanceof Error ? error.message : String(error) });
  }
  return null;
}

function logEventLatency(event: UtteranceTranscribed): void {
  const occurredAtMs = Date.parse(event.occurred_at);
  if (Number.isNaN(occurredAtMs)) return;
  appLogger.latency("subtitle", `Utterance seq=${event.seq} delivered`, Date.now() - occurredAtMs, { detail: event.is_final ? "final" : "partial" });
}
// === ANCHOR: USE_LIVE_SUBTITLE_STREAM_END ===
