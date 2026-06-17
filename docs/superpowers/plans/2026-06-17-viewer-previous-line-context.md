# Participant Viewer Previous-Line Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the previous subtitle line (Korean only, dimmed, smaller) above the current subtitle in the participant web viewer, mirroring the operator desktop preview.

**Architecture:** Pure viewer rendering in `apps/web`. The data already exists client-side — `useViewerWS` returns `utterances: UtteranceTranscribed[]` (last 50). Add a pure `previousUtterance` helper (line-for-line port of the desktop `previousSubtitle`) and render its `text_ko` above the current line in `SubtitleView`. No backend, protocol, or data-flow change.

**Tech Stack:** React + TypeScript + Tailwind (Vite). `apps/web` has NO test runner (no vitest, no `*.test.*`); verification is `tsc --noEmit` + `vite build` (via `pnpm --filter @yeson-meet/web build`) staying green, plus visual check. We do NOT add a web test runner for this slice (YAGNI; the helper mirrors already-proven desktop logic).

**Spec:** `docs/superpowers/specs/2026-06-17-viewer-previous-line-context-design.md`

---

## File Structure

- `apps/web/src/lib/utterances.ts` — add `previousUtterance(utterances, latestSeq)` pure helper next to the existing `upsertUtterance`/`latestUtterance`.
- `apps/web/src/components/SubtitleView.tsx` — consume `utterances`, compute `previous`, render the previous Korean line above the current line.

Both edits stay inside their existing anchors (`WEB_UTTERANCES_*`, `SUBTITLEVIEW_*`). Smallest possible patch.

---

## Task 1: `previousUtterance` helper

**Files:**
- Modify: `apps/web/src/lib/utterances.ts` (inside the `WEB_UTTERANCES` anchor, after `latestUtterance`, before the `WEB_UTTERANCES_END` anchor)

Note: `apps/web` has no test runner, so this is verified by typecheck/build, not a unit test. The function is a verbatim port of the proven desktop `previousSubtitle` (`apps/desktop/src/console/LiveSubtitlePreview.tsx:95-103`).

- [ ] **Step 1: Add the helper**

In `apps/web/src/lib/utterances.ts`, after the `latestUtterance` function and before the closing `// === ANCHOR: WEB_UTTERANCES_END ===` line, add:

```ts
export function previousUtterance(
  utterances: UtteranceTranscribed[],
  latestSeq: number | null,
): UtteranceTranscribed | null {
  if (utterances.length < 2 || latestSeq === null) return null;
  for (let index = utterances.length - 2; index >= 0; index -= 1) {
    const item = utterances[index];
    if (item && item.seq !== latestSeq) return item;
  }
  return null;
}
```

(`UtteranceTranscribed` is already imported at the top of the file. `utterances` is already sorted ascending by `seq` via `upsertUtterance`, so walking back from the second-to-last entry yields the most recent different-seq utterance.)

- [ ] **Step 2: Typecheck/build to verify it compiles**

Run: `pnpm --filter @yeson-meet/web build`
Expected: `tsc --noEmit` passes and `vite build` succeeds (no type errors, exit 0). The new export is unused until Task 2 — that is fine (it's an exported function, not an unused local, so no TS error).

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/lib/utterances.ts
git commit -m "feat(viewer): add previousUtterance helper"
```

---

## Task 2: Render previous Korean line in `SubtitleView`

**Files:**
- Modify: `apps/web/src/components/SubtitleView.tsx`

Current relevant code (for reference):
- Imports (lines 1-3): `usePacedSubtitle`, `useViewerWS`.
- Line 7: `const { latest: streamLatest, connected, ended, error } = useViewerWS(token);`
- Line 10: `const latest = usePacedSubtitle(streamLatest);`
- The final render branch (lines 38-43):
  ```tsx
  ) : (
    <div className="text-center max-w-5xl space-y-4">
      <div className="text-5xl font-bold leading-tight">{latest.text_ko}</div>
      <div className="text-2xl opacity-60">{latest.text_en}</div>
    </div>
  )}
  ```

- [ ] **Step 1: Import the helper**

At the top of `apps/web/src/components/SubtitleView.tsx`, add an import (after the existing hook imports):

```tsx
import { previousUtterance } from "../lib/utterances";
```

- [ ] **Step 2: Consume `utterances` and compute `previous`**

Change the `useViewerWS` destructure (line 7) to also pull `utterances`:

```tsx
  const { latest: streamLatest, connected, ended, error, utterances } = useViewerWS(token);
```

Immediately after `const latest = usePacedSubtitle(streamLatest);` (line 10), add:

```tsx
  const previous = previousUtterance(utterances, latest?.seq ?? null);
```

(`previous` is computed against the PACED `latest.seq`, so it reflects the turn currently displayed — matching the operator preview and staying stable across same-seq partial updates.)

- [ ] **Step 3: Render the previous Korean line above the current line**

Replace the final render branch (the `) : (` … `)}` block at lines 38-43) with:

```tsx
        ) : (
          <div className="text-center max-w-5xl space-y-4">
            {previous?.text_ko ? (
              <div className="text-2xl md:text-3xl text-slate-400 opacity-50 leading-snug">
                {previous.text_ko}
              </div>
            ) : null}
            <div className="text-5xl font-bold leading-tight">{latest.text_ko}</div>
            <div className="text-2xl opacity-60">{latest.text_en}</div>
          </div>
        )}
```

(Korean only for the previous line; current line unchanged. The previous line is clearly subordinate — smaller and dimmed — so the big current line stays glanceable. Renders only when a previous distinct-seq utterance exists, so the first subtitle shows no context line.)

- [ ] **Step 4: Typecheck/build to verify**

Run: `pnpm --filter @yeson-meet/web build`
Expected: `tsc --noEmit` passes (no unused-import or type errors — `utterances` and `previous` are now both used) and `vite build` succeeds, exit 0.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/SubtitleView.tsx
git commit -m "feat(viewer): show previous-line context in participant viewer"
```

---

## Final verification

- [ ] **Build is green**

Run: `pnpm --filter @yeson-meet/web build`
Expected: exit 0, no type errors.

- [ ] **Visual smoke (manual / when a viewer is running)**

With a live session emitting subtitles in the web viewer:
- First subtitle: only the big current line shows (no previous line).
- After a second distinct-seq turn arrives: the prior Korean line appears dimmed/smaller directly above the current big line; the English source of the current line still shows below.
- On a same-seq partial→final update, the previous line does not flicker (it is keyed off the paced `latest.seq`, which is unchanged across partials).

- [ ] **Working tree clean of unrelated changes**

Run: `git status --short`
Expected: only the prior-session leftovers (`PROJECT_CONTEXT.md`, `apps/desktop/scripts/vm_dump.py`, `bun.lock`) remain unstaged — do not commit those.
