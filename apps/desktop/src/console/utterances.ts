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
    // Stability: a non-final partial must not SHRINK the visible text. A
    // non-incremental partial can briefly reset to a short prefix; replacing a
    // longer line with it pulses the box smaller→larger mid-caption (flicker).
    // Hold the longer text until a longer partial or the final arrives (finals
    // always win above).
    if (!next.is_final && !existing.is_final) {
      const existingLen = (existing.text_ko || existing.text_en || "").length;
      const nextLen = (next.text_ko || next.text_en || "").length;
      if (nextLen < existingLen) return current;
    }
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
