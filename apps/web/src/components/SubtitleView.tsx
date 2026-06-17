// === ANCHOR: SUBTITLEVIEW_START ===
import { usePacedSubtitle } from "../hooks/usePacedSubtitle";
import { useViewerWS } from "../hooks/useViewerWS";
import { previousUtterance } from "../lib/utterances";

// === ANCHOR: SUBTITLEVIEW_SUBTITLEVIEW_START ===
export function SubtitleView({ token }: { token: string }) {
  const { latest: streamLatest, connected, ended, error, utterances } = useViewerWS(token);
  // 자막이 너무 빨리 다음 seq로 갱신되면 사용자가 읽을 시간이 부족하다.
  // 길이에 비례한 최소 표시 시간을 보장하면서 다음 자막은 큐에 보관해 노출.
  const latest = usePacedSubtitle(streamLatest);
  // 표시 중인(페이싱된) seq 기준 직전 발화 — 운영자 프리뷰와 동일한 catch-up.
  const previous = previousUtterance(utterances, latest?.seq ?? null);

  return (
    <main className="min-h-screen bg-slate-900 text-slate-100 flex flex-col">
      <header className="px-6 py-3 flex items-center justify-between border-b border-slate-800">
        <div className="text-lg font-semibold">yeson-meet</div>
        <div className="flex items-center gap-2 text-sm">
          <span
            className={
              "inline-block w-2 h-2 rounded-full " +
              (connected ? "bg-emerald-400" : "bg-amber-400 animate-pulse")
            }
          />
          <span className="opacity-70">
            {ended ? "ENDED" : connected ? "LIVE" : "CONNECTING"}
          </span>
        </div>
      </header>
      <section className="flex-1 flex items-center justify-center px-8">
        {ended ? (
          <div className="text-center space-y-4">
            <div className="text-5xl font-bold">회의 종료됨</div>
            <div className="text-xl text-slate-400">자막 기록이 저장되었습니다.</div>
          </div>
        ) : error ? (
          <div className="text-rose-400 text-2xl">{error}</div>
        ) : !latest ? (
          <div className="text-slate-400 text-3xl">자막을 기다리는 중…</div>
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
      </section>
    </main>
  );
}
// === ANCHOR: SUBTITLEVIEW_SUBTITLEVIEW_END ===
// === ANCHOR: SUBTITLEVIEW_END ===
