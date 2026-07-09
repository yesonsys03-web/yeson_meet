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
          if ((evt.type as string) === "session.ended") {
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
