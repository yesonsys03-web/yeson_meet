import { useEffect, useRef, useState } from "react";
import type { UtteranceTranscribed } from "../types/events";
import { fetchBackfill, viewerWsUrl } from "../lib/api";

export type ViewerState = {
  utterances: UtteranceTranscribed[];
  latest: UtteranceTranscribed | null;
  connected: boolean;
  error: string | null;
};

export function useViewerWS(token: string): ViewerState {
  const [state, setState] = useState<ViewerState>({
    utterances: [],
    latest: null,
    connected: false,
    error: null,
  });
  const lastSeqRef = useRef<number>(0);

  useEffect(() => {
    if (!token) return;
    let active = true;
    let ws: WebSocket | null = null;
    let backoff = 1000;

    async function start() {
      try {
        const backfill = await fetchBackfill(token, null);
        if (!active) return;
        const sorted = [...backfill].sort((a, b) => a.seq - b.seq);
        const last = sorted[sorted.length - 1] ?? null;
        if (last) lastSeqRef.current = last.seq;
        setState((s) => ({
          ...s,
          utterances: sorted,
          latest: last,
          error: null,
        }));
      } catch (e) {
        if (!active) return;
        setState((s) => ({ ...s, error: String(e) }));
      }
      connect();
    }

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
          const evt = JSON.parse(e.data) as UtteranceTranscribed;
          if (evt.type !== "utterance.transcribed") return;
          if (evt.seq <= lastSeqRef.current) return; // dedupe / out-of-order safety
          lastSeqRef.current = evt.seq;
          setState((s) => ({
            ...s,
            utterances: [...s.utterances, evt].slice(-50),
            latest: evt,
          }));
        } catch {}
      };
      ws.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        if (!active) return;
        setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 30000);
      };
      ws.onerror = () => {
        setState((s) => ({ ...s, error: "ws error" }));
      };
    }

    start();
    return () => {
      active = false;
      ws?.close();
    };
  }, [token]);

  return state;
}
