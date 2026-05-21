// === ANCHOR: USE_PACED_SUBTITLE_START ===
import { useEffect, useRef, useState } from "react";
import type { UtteranceTranscribed } from "./types";

// 사용자가 자막을 읽을 최소 시간을 보장하기 위한 표시 페이서.
// stream.latest가 너무 빨리 다음 seq로 갱신되면 이전 자막이 화면에서
// 사라지기 전에 읽을 시간이 부족하다. 이 훅은 현재 표시 중인 자막을
// 최소 표시 시간까지 유지하고, 그 사이 도착하는 새 seq는 큐에 보관해
// 순차 노출한다.
const BASE_DISPLAY_MS = 1500;
const PER_CHAR_MS = 80;
const MAX_DISPLAY_MS = 8000;
const QUEUE_MAX = 5;

function computeDisplayMs(utterance: UtteranceTranscribed): number {
  const text = utterance.text_ko || utterance.text_en || "";
  return Math.min(BASE_DISPLAY_MS + text.length * PER_CHAR_MS, MAX_DISPLAY_MS);
}

type Slot = {
  utterance: UtteranceTranscribed;
  shownAtMs: number;
  displayMs: number;
};

export function usePacedSubtitle(latest: UtteranceTranscribed | null): UtteranceTranscribed | null {
  const [slot, setSlot] = useState<Slot | null>(null);
  const queueRef = useRef<UtteranceTranscribed[]>([]);

  useEffect(() => {
    if (!latest) {
      setSlot(null);
      queueRef.current = [];
      return;
    }
    setSlot((current) => {
      if (!current) {
        return {
          utterance: latest,
          shownAtMs: performance.now(),
          displayMs: computeDisplayMs(latest),
        };
      }
      if (current.utterance.seq === latest.seq) {
        const newDisplayMs = computeDisplayMs(latest);
        return {
          utterance: latest,
          shownAtMs: current.shownAtMs,
          displayMs: Math.max(current.displayMs, newDisplayMs),
        };
      }
      const elapsed = performance.now() - current.shownAtMs;
      if (elapsed >= current.displayMs) {
        return {
          utterance: latest,
          shownAtMs: performance.now(),
          displayMs: computeDisplayMs(latest),
        };
      }
      const queue = queueRef.current;
      const existing = queue.findIndex((item) => item.seq === latest.seq);
      if (existing >= 0) {
        queue[existing] = latest;
      } else {
        queue.push(latest);
      }
      while (queue.length > QUEUE_MAX) {
        queue.shift();
      }
      return current;
    });
  }, [latest]);

  useEffect(() => {
    if (!slot) return;
    const elapsed = performance.now() - slot.shownAtMs;
    const remaining = Math.max(0, slot.displayMs - elapsed);
    const timer = window.setTimeout(() => {
      const next = queueRef.current.shift();
      if (next) {
        setSlot({
          utterance: next,
          shownAtMs: performance.now(),
          displayMs: computeDisplayMs(next),
        });
      }
    }, remaining);
    return () => window.clearTimeout(timer);
  }, [slot]);

  return slot?.utterance ?? null;
}
// === ANCHOR: USE_PACED_SUBTITLE_END ===
