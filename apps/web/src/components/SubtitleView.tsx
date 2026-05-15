import { useViewerWS } from "../hooks/useViewerWS";

export function SubtitleView({ token }: { token: string }) {
  const { latest, connected, error } = useViewerWS(token);

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
          <span className="opacity-70">{connected ? "LIVE" : "CONNECTING"}</span>
        </div>
      </header>
      <section className="flex-1 flex items-center justify-center px-8">
        {error ? (
          <div className="text-rose-400 text-2xl">{error}</div>
        ) : !latest ? (
          <div className="text-slate-400 text-3xl">자막을 기다리는 중…</div>
        ) : (
          <div className="text-center max-w-5xl space-y-4">
            <div className="text-5xl font-bold leading-tight">{latest.text_ko}</div>
            <div className="text-2xl opacity-60">{latest.text_en}</div>
          </div>
        )}
      </section>
    </main>
  );
}
