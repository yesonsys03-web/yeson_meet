// === ANCHOR: USE_PACED_SUBTITLE_START ===
import { useEffect, useRef, useState } from "react";
import type { UtteranceTranscribed } from "./types";

// 표시 페이서 — 지연(latency)을 묶으면서 읽기 시간을 지킨다.
// 규칙:
//  1) 다음 자막이 없으면 현재 자막은 계속 떠 있다 → 한가할 땐 읽을 시간 무제한.
//  2) 새 자막이 와도 현재 자막이 최소 표시시간(MIN_READ_MS)을 못 채웠으면
//     그때만 잠깐 대기시킨다. 대기 중 더 새 자막이 오면 중간 것은 버리고
//     항상 "가장 최신"만 보관 → 연속 발화로 밀려도 지연은 최대 MIN_READ_MS
//     한 박자로 상한이 걸린다(이전: 큐 5개 × 최대 8초로 무한정 누적).
//  3) 같은 seq의 갱신(partial→final)은 제자리에서 텍스트만 교체.
const MIN_READ_MS = 2200;

type Slot = {
  utterance: UtteranceTranscribed;
  shownAtMs: number;
};

export function usePacedSubtitle(latest: UtteranceTranscribed | null): UtteranceTranscribed | null {
  const [slot, setSlot] = useState<Slot | null>(null);
  // 대기 중인 "가장 최신" 자막 1개만 보관(중간 자막은 버려 지연을 묶는다).
  const pendingRef = useRef<UtteranceTranscribed | null>(null);

  useEffect(() => {
    if (!latest) {
      setSlot(null);
      pendingRef.current = null;
      return;
    }
    setSlot((current) => {
      if (!current) {
        pendingRef.current = null;
        return { utterance: latest, shownAtMs: performance.now() };
      }
      if (current.utterance.seq === latest.seq) {
        // 같은 발화의 갱신(partial→final): 제자리 교체, 표시 시작 시각 유지.
        return { utterance: latest, shownAtMs: current.shownAtMs };
      }
      const elapsed = performance.now() - current.shownAtMs;
      if (elapsed >= MIN_READ_MS) {
        // 현재 자막을 충분히 읽을 시간이 지났으면 새 자막 즉시 표시(지연 0).
        pendingRef.current = null;
        return { utterance: latest, shownAtMs: performance.now() };
      }
      // 아직 못 읽었으면 잠깐 대기 — 항상 최신만 보관(중간 자막은 버림).
      pendingRef.current = latest;
      return current;
    });
  }, [latest]);

  useEffect(() => {
    if (!slot) return;
    const elapsed = performance.now() - slot.shownAtMs;
    const remaining = Math.max(0, MIN_READ_MS - elapsed);
    const timer = window.setTimeout(() => {
      const next = pendingRef.current;
      if (next && next.seq !== slot.utterance.seq) {
        pendingRef.current = null;
        setSlot({ utterance: next, shownAtMs: performance.now() });
      }
    }, remaining);
    return () => window.clearTimeout(timer);
  }, [slot]);

  return slot?.utterance ?? null;
}
// === ANCHOR: USE_PACED_SUBTITLE_END ===
