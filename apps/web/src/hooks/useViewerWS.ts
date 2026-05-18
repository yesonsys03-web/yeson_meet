// === ANCHOR: USEVIEWERWS_START ===
import { useEffect, useRef, useState } from "react";
import type { DomainEvent, UtteranceTranscribed } from "../types/events";
import { fetchBackfill, viewerWsUrl } from "../lib/api";
import { latestUtterance, upsertUtterance } from "../lib/utterances";

export type ViewerState = {
  utterances: UtteranceTranscribed[];
  latest: UtteranceTranscribed | null;
  connected: boolean;
  ended: boolean;
  error: string | null;
};

// === ANCHOR: USEVIEWERWS_USEVIEWERWS_START ===
export function useViewerWS(token: string): ViewerState {
  const [state, setState] = useState<ViewerState>({
    utterances: [],
    latest: null,
    connected: false,
    ended: false,
    error: null,
  });
  const lastSeqRef = useRef<number>(0);

  useEffect(() => {
    if (!token) return;
    let active = true;
    let ws: WebSocket | null = null;
    let backoff = 1000;
    let sessionEnded = false;

    // === ANCHOR: USEVIEWERWS_START_START ===
    async function start() {
      try {
        const backfill = await fetchBackfill(token, null);
        if (!active) return;
        const sorted = [...backfill.utterances]
          .sort((a, b) => a.seq - b.seq)
          .reduce<UtteranceTranscribed[]>(upsertUtterance, []);
        const last = latestUtterance(sorted);
        if (last) lastSeqRef.current = last.seq;
        setState((s) => ({
          ...s,
          utterances: sorted,
          latest: last,
          ended: backfill.session_status === "ended",
          error: null,
        }));
        if (backfill.session_status === "ended") {
          sessionEnded = true;
          return;
        }
      } catch (e) {
        if (!active) return;
        setState((s) => ({ ...s, error: String(e) }));
      }
      connect();
    }
    // === ANCHOR: USEVIEWERWS_START_END ===

    // === ANCHOR: USEVIEWERWS_CONNECT_START ===
    function connect() {
      if (!active) return;
      const url = viewerWsUrl(token);
      ws = new WebSocket(url);
      ws.onopen = () => {
        backoff = 1000;
        setState((s) => ({ ...s, connected: true, error: null }));
      };
      ws.onmessage = (e) => {
        try {
          const evt = JSON.parse(e.data) as DomainEvent;
          if (evt.type === "session.ended") {
            sessionEnded = true;
            setState((s) => ({ ...s, connected: false, ended: true, error: null }));
            ws?.close();
            return;
          }
          if (evt.type !== "utterance.transcribed") return;
          if (evt.seq < lastSeqRef.current) return; // out-of-order safety; equal seq may replace partial
          lastSeqRef.current = Math.max(lastSeqRef.current, evt.seq);
          setState((s) => {
            const utterances = upsertUtterance(s.utterances, evt);
            return {
              ...s,
              utterances,
              latest: latestUtterance(utterances),
            };
          });
        } catch {}
      };
      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        if (!active) return;
        if (sessionEnded) return;
        setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 30000);
      };
      ws.onerror = () => {
        setState((s) => ({ ...s, error: "ws error" }));
      };
    }
    // === ANCHOR: USEVIEWERWS_CONNECT_END ===

    start();
    return () => {
      active = false;
      ws?.close();
    };
// === ANCHOR: USEVIEWERWS_USEVIEWERWS_END ===
  }, [token]);

  return state;
}
// === ANCHOR: USEVIEWERWS_END ===
