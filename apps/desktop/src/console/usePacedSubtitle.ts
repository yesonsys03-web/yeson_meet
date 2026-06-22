// === ANCHOR: USE_PACED_SUBTITLE_START ===
import { useEffect, useRef, useState } from "react";
import type { UtteranceTranscribed } from "./types";

// 표시 페이서 — 누락 0 + 읽기시간 보장.
// 정책:
//  · 들어온 모든 발화(seq)를 순서대로 한 개씩 재생한다. 절대 건너뛰지 않는다.
//  · 각 자막은 글자수에 비례한 읽기시간(최소 MIN_FLOOR_MS) 동안 떠 있다 →
//    "다 읽기 전에 사라짐" 방지. 다음 자막이 없으면 계속 떠 있다(idle).
//  · 뒤로 밀리면(대기 큐가 쌓이면) 각 자막의 표시시간을 바닥(MIN_FLOOR_MS)까지
//    점진 압축해 큐를 비워 따라잡는다 — 하지만 버리지는 않는다. (읽기 속도가
//    말하기 속도보다 빠르므로 보통 한두 개 차이로 수렴한다.)
//  · 같은 seq의 partial→final 갱신은 화면에서 제자리 텍스트 교체.
//  · 최초 진입 시에는 백필(과거 자막)을 되감지 않고 최신 seq부터 시작한다.
const MIN_FLOOR_MS = 1200;
const BASE_READ_MS = 1400;
const PER_CHAR_MS = 70;
const MAX_READ_MS = 5500;
const CATCHUP_STEP_MS = 350; // 대기 1개 늘 때마다 표시시간을 이만큼 깎아 따라잡음

function textOf(utterance: UtteranceTranscribed): string {
  return utterance.text_ko || utterance.text_en || "";
}

function displayMsFor(utterance: UtteranceTranscribed | null, backlog: number): number {
  if (!utterance) return MIN_FLOOR_MS;
  const read = Math.min(
    Math.max(BASE_READ_MS + textOf(utterance).length * PER_CHAR_MS, MIN_FLOOR_MS),
    MAX_READ_MS,
  );
  const compressed = read - Math.max(0, backlog - 1) * CATCHUP_STEP_MS;
  return Math.max(MIN_FLOOR_MS, Math.min(read, compressed));
}

function nextSeqAbove(utterances: UtteranceTranscribed[], seq: number): number | null {
  // utterances는 seq 오름차순 → seq보다 큰 첫 항목이 "바로 다음".
  for (const item of utterances) {
    if (item.seq > seq) return item.seq;
  }
  return null;
}

function countSeqsAbove(utterances: UtteranceTranscribed[], seq: number): number {
  let count = 0;
  for (const item of utterances) {
    if (item.seq > seq) count += 1;
  }
  return count;
}

export function usePacedSubtitle(utterances: UtteranceTranscribed[]): UtteranceTranscribed | null {
  const [shownSeq, setShownSeq] = useState<number | null>(null);
  const shownAtRef = useRef(0);
  const initRef = useRef(false);

  const lastUtterance = utterances[utterances.length - 1];
  const firstUtterance = utterances[0];
  const maxSeq = lastUtterance ? lastUtterance.seq : null;
  const minSeq = firstUtterance ? firstUtterance.seq : null;

  // 최초 진입: 백필 과거 자막을 되감지 않고 최신부터 시작.
  useEffect(() => {
    if (initRef.current || maxSeq === null) return;
    initRef.current = true;
    shownAtRef.current = performance.now();
    setShownSeq(maxSeq);
  }, [maxSeq]);

  // 현재 자막이 보관 창(MAX_UTTERANCES)에서 잘려나갔으면 가장 오래된 보관분으로 복구.
  useEffect(() => {
    if (shownSeq === null || minSeq === null) return;
    if (shownSeq < minSeq) {
      shownAtRef.current = performance.now();
      setShownSeq(minSeq);
    }
  }, [shownSeq, minSeq]);

  // 표시시간이 지나고 더 높은 seq가 있으면 "바로 다음" seq로 한 칸 전진(건너뛰지 않음).
  useEffect(() => {
    if (shownSeq === null || maxSeq === null || maxSeq <= shownSeq) return;
    const current = utterances.find((item) => item.seq === shownSeq) ?? null;
    const backlog = countSeqsAbove(utterances, shownSeq);
    const displayMs = displayMsFor(current, backlog);
    const remaining = Math.max(0, displayMs - (performance.now() - shownAtRef.current));
    const timer = window.setTimeout(() => {
      const next = nextSeqAbove(utterances, shownSeq);
      if (next !== null) {
        shownAtRef.current = performance.now();
        setShownSeq(next);
      }
    }, remaining);
    return () => window.clearTimeout(timer);
  }, [shownSeq, maxSeq, utterances]);

  if (shownSeq === null) return null;
  return utterances.find((item) => item.seq === shownSeq) ?? null;
}
// === ANCHOR: USE_PACED_SUBTITLE_END ===
