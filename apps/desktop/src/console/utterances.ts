// === ANCHOR: CONSOLE_UTTERANCES_START ===
import type { UtteranceTranscribed } from "./types";

// Keep a generous backlog so the paced display can show every subtitle in order
// (no drops) even when it falls a bit behind under continuous speech.
const MAX_UTTERANCES = 200;

export function upsertUtterance(current: UtteranceTranscribed[], next: UtteranceTranscribed): UtteranceTranscribed[] {
  const existingIndex = current.findIndex((item) => item.seq === next.seq);
  if (existingIndex >= 0) {
    const existing = current[existingIndex];
    if (!existing) return current;
    if (existing.is_final && !next.is_final) return current;
    const copy = [...current];
    copy[existingIndex] = next;
    return copy.slice(-MAX_UTTERANCES);
  }
  return [...current, next].sort((a, b) => a.seq - b.seq).slice(-MAX_UTTERANCES);
}

export function latestUtterance(utterances: UtteranceTranscribed[]): UtteranceTranscribed | null {
  return utterances.reduce<UtteranceTranscribed | null>((latest, item) => {
    if (!latest) return item;
    return item.seq >= latest.seq ? item : latest;
  }, null);
}
// === ANCHOR: CONSOLE_UTTERANCES_END ===
