// === ANCHOR: CONSOLE_UTTERANCES_START ===
import type { UtteranceTranscribed } from "./types";

const MAX_UTTERANCES = 50;

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
  return utterances[utterances.length - 1] ?? null;
}
// === ANCHOR: CONSOLE_UTTERANCES_END ===
