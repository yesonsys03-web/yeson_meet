# Participant Viewer — Previous-Line Context

- Date: 2026-06-17
- Status: Approved (design)
- Branch: `topyeson`
- Related: operator desktop preview `apps/desktop/src/console/LiveSubtitlePreview.tsx`
  (already renders previous line above current via `previousSubtitle`)

## Problem

The participant web viewer (`apps/web/src/components/SubtitleView.tsx`) shows only
the single current subtitle line (`latest.text_ko` big + `latest.text_en` small).
When several people speak in turns and a line scrolls by, a participant who glanced
away loses the previous turn entirely. The operator desktop preview already solves
this by rendering the previous subtitle (dimmed, above the current one); the
participant viewer does not.

This brings the participant viewer to parity with the operator preview: one line of
previous context above the current subtitle. It is a turn-catch-up aid, NOT speaker
identification.

## Goals

- Show the previous subtitle line (Korean only), dimmed and smaller, above the
  current subtitle in the participant web viewer.
- Mirror the operator preview's behavior: "previous" is the utterance before the
  currently-displayed (paced) subtitle's `seq`.
- No backend, protocol, or data-flow changes — the data is already client-side.

## Non-Goals

- No multi-line scroll history / transcript (that competes with the big glanceable
  current line, and the full transcript is already in the post-meeting MD report).
- No previous-line English (clutter); previous line is Korean only.
- No speaker labels / diarization (different feature, deliberately out of scope).
- No change to the current line (keeps `text_ko` big + `text_en` small).

## Approach

Pure viewer rendering. `useViewerWS` already returns `utterances: UtteranceTranscribed[]`
(last 50, maintained by `upsertUtterance`). `SubtitleView` currently ignores that array
and uses only `latest`. We add a small pure helper to compute the previous utterance and
render it above the current line.

Considered and rejected:
- Computing "previous" from the raw stream's newest seq — wrong, because `usePacedSubtitle`
  may be displaying an older seq than the stream has received. We must key off the
  **displayed (paced) `latest.seq`**, matching the operator preview.
- Reusing the desktop `previousSubtitle` by import — rejected; web and desktop are
  separate apps/packages with their own `utterances` modules. Mirror the logic in web's
  own `lib/utterances.ts` to keep the apps independent.

## Design

### 1. Helper — `apps/web/src/lib/utterances.ts`

Add a pure function mirroring the desktop `previousSubtitle` logic:

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

Semantics: walk back from the second-to-last entry, return the first utterance whose
`seq` differs from `latestSeq`; `null` when fewer than 2 entries or no different-seq
item exists. `utterances` is already sorted ascending by `seq` (via `upsertUtterance`).

### 2. Render — `apps/web/src/components/SubtitleView.tsx`

- Destructure `utterances` from `useViewerWS` (already in `ViewerState`).
- Compute `const previous = previousUtterance(utterances, latest?.seq ?? null);`
  where `latest` is the paced subtitle (`usePacedSubtitle(streamLatest)`), as today.
- In the rendered subtitle block (the `latest` branch), render, above the current
  Korean line, a previous-context line when `previous?.text_ko` is present:

```tsx
<div className="text-center max-w-5xl space-y-4">
  {previous?.text_ko ? (
    <div className="text-2xl md:text-3xl text-slate-400 opacity-50 leading-snug">
      {previous.text_ko}
    </div>
  ) : null}
  <div className="text-5xl font-bold leading-tight">{latest.text_ko}</div>
  <div className="text-2xl opacity-60">{latest.text_en}</div>
</div>
```

(Korean only for the previous line; the current line is unchanged. Exact Tailwind
sizes are tuned in implementation but the previous line stays clearly subordinate to
the big current line so glanceability is preserved.)

### 3. Behavior notes

- `previous` is empty until at least two distinct seqs exist → first subtitle shows
  no context line (correct).
- When a partial replaces a same-seq final-in-progress, `latest.seq` is unchanged, so
  `previous` stays stable (no flicker on partial updates).
- On `ended`/`error`/loading states the existing branches are untouched (no previous
  line there).

## Testing

`apps/web` has no test runner (no vitest config, no `*.test.*` files, no `test` script).
Standing up a test toolchain for one 6-line pure function that is a verbatim mirror of
the already-shipped, already-tested desktop `previousSubtitle` would be disproportionate
(YAGNI) and would unilaterally restructure the web app. So this slice does NOT add a web
test runner. Verification instead:

- Typecheck + build stay green: the repo's web build command (e.g.
  `pnpm --filter @yeson-meet/web build`, which runs `tsc` then `vite build` — confirm the
  exact script in `apps/web/package.json` during planning).
- The `previousUtterance` helper is a line-for-line port of desktop `previousSubtitle`
  (`apps/desktop/src/console/LiveSubtitlePreview.tsx`), whose behavior is already proven
  in production — correctness rides on that parity.
- The component change is visual; verified in the running viewer (previous Korean line
  appears dimmed above the current line once a second distinct-seq turn arrives, and is
  absent for the first subtitle).

## Open questions

None. (Korean-only previous line confirmed; single line, no history.)
