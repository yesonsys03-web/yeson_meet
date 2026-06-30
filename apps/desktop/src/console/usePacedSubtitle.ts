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
export const MIN_FLOOR_MS = 1200;
const BASE_READ_MS = 1400;
const PER_CHAR_MS = 70;
const MAX_READ_MS = 5500;
const CATCHUP_STEP_MS = 350; // 대기 1개 늘 때마다 표시시간을 이만큼 깎아 따라잡음
// 백로그가 쌓이면 읽기시간 바닥을 MIN_FLOOR_MS에서 이 값까지 낮춰 큐를 빠르게
// 배출한다. Gemini가 ~10초 묶음으로 자막을 쏟아낼 때 1.2초 바닥으로는 못 따라잡아
// 화면이 수 초 뒤처졌다(7초→12초 회귀의 원인). 밀릴 때만 짧게 떠 다음 묶음 전에
// 최신까지 따라잡으므로 지연을 파이프라인 수준으로 되돌린다 — 누락은 여전히 0.
// Floor on per-line display time even under a backlog. 450ms drained fast but
// flashed by unreadably during the cold-start burst (rapid segment cycling →
// many lines at once). 1000ms guarantees each line is on screen ≥1s — readable
// even during a burst — while still draining a small cold-start backlog inside a
// turn or two. Steady state (1 line per ~12s turn) never hits this floor, so
// there is no added lag once warmed up.
export const CATCHUP_FLOOR_MS = 1000;

function textOf(utterance: UtteranceTranscribed): string {
  return utterance.text_ko || utterance.text_en || "";
}

export function displayMsFor(utterance: UtteranceTranscribed | null, backlog: number): number {
  if (!utterance) return MIN_FLOOR_MS;
  const read = Math.min(
    Math.max(BASE_READ_MS + textOf(utterance).length * PER_CHAR_MS, MIN_FLOOR_MS),
    MAX_READ_MS,
  );
  const over = Math.max(0, backlog - 1);
  const compressed = read - over * CATCHUP_STEP_MS;
  // 따라잡는 동안에는 읽기 바닥도 함께 낮춘다(MIN_FLOOR→CATCHUP_FLOOR).
  const floor = Math.max(CATCHUP_FLOOR_MS, MIN_FLOOR_MS - over * CATCHUP_STEP_MS);
  return Math.max(floor, Math.min(read, compressed));
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

  // 현재 자막 seq가 보관 범위를 벗어나면 복구한다:
  //  · shownSeq > maxSeq: 회의 종료→재시작 등으로 seq가 낮게 리셋된 새 세션.
  //    이전 회의의 높은 seq를 가리킨 채면 새 회의 자막을 find 못 해 화면이
  //    빈 채로 남는다 → 최신(maxSeq)으로 스냅.
  //  · shownSeq < minSeq: 보관 창(MAX_UTTERANCES)에서 잘려나감 → 가장 오래된 보관분.
  useEffect(() => {
    if (shownSeq === null || minSeq === null || maxSeq === null) return;
    if (shownSeq > maxSeq) {
      shownAtRef.current = performance.now();
      setShownSeq(maxSeq);
    } else if (shownSeq < minSeq) {
      shownAtRef.current = performance.now();
      setShownSeq(minSeq);
    }
  }, [shownSeq, minSeq, maxSeq]);

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
