// === ANCHOR: USE_OPERATOR_SUBTITLES_START ===
// 진행자 자막 미리보기 — REST 폴링(2.5s). 이전 /ws/operator는 JWT가 URL 쿼리에
// 실려 터널 노출에 부적합해 제거했다(참석자 뷰어 WS는 무변경·실시간).
import { useEffect, useRef, useState } from "react";
import type { UtteranceTranscribed } from "../types/events";
import { latestUtterance, upsertUtterance } from "../lib/utterances";
import { fetchOperatorBackfill } from "./captureApi";

const POLL_INTERVAL_MS = 2500;

export type OperatorSubtitles = {
  utterances: UtteranceTranscribed[];
  latest: UtteranceTranscribed | null;
  connected: boolean;
};

export function useOperatorSubtitles(sessionId: string | null, operatorToken: string | null): OperatorSubtitles {
  const [state, setState] = useState<OperatorSubtitles>({ utterances: [], latest: null, connected: false });
  const endedRef = useRef(false);

  useEffect(() => {
    if (!sessionId || !operatorToken) {
      setState({ utterances: [], latest: null, connected: false });
      return;
    }
    endedRef.current = false;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      if (!active || endedRef.current) return;
      try {
        const backfill = await fetchOperatorBackfill(operatorToken!, sessionId!);
        if (!active) return;
        const sorted = [...backfill.utterances]
          .sort((a, b) => a.seq - b.seq)
          .reduce<UtteranceTranscribed[]>(upsertUtterance, []);
        setState({ utterances: sorted, latest: latestUtterance(sorted), connected: true });
        if (backfill.session_status === "ended") {
          endedRef.current = true;
          setState((s) => ({ ...s, connected: false }));
          return;
        }
      } catch {
        if (active) setState((s) => ({ ...s, connected: false }));
      }
      timer = setTimeout(poll, POLL_INTERVAL_MS);
    }

    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [sessionId, operatorToken]);

  return state;
}
// === ANCHOR: USE_OPERATOR_SUBTITLES_END ===
