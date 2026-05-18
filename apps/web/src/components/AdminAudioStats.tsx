import { useEffect, useState } from "react";

type Snapshot = {
  session_id: string;
  total_bytes: number;
  total_chunks: number;
  chunks_per_sec_1s: number;
  last_seq: number | null;
  started_at: string | null;
  stopped_at: string | null;
  stopped_reason: string | null;
  age_ms: number | null;
};

const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

function fmtBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(2)} MB`;
}

export function AdminAudioStats({ sessionId, token }: { sessionId: string; token: string }) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function poll() {
      try {
        const u = new URL(
          `${API_BASE}/api/v1/sessions/${encodeURIComponent(sessionId)}/audio_stats`,
          window.location.origin,
        );
        const res = await fetch(u.toString(), {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as Snapshot;
        if (active) {
          setSnap(data);
          setError(null);
        }
      } catch (e) {
        if (active) setError(String(e));
      }
    }
    poll();
    const id = setInterval(poll, 1000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [sessionId, token]);

  return (
    <main className="min-h-screen bg-slate-900 text-slate-100 p-8">
      <h1 className="text-3xl font-bold mb-6">Audio stats</h1>
      <div className="text-sm opacity-60 mb-4 font-mono">session={sessionId}</div>
      {error ? (
        <div className="text-rose-400 text-2xl">{error}</div>
      ) : !snap ? (
        <div className="text-slate-400 text-2xl">loading…</div>
      ) : (
        <div className="space-y-3 text-3xl font-bold">
          <div>
            초당 청크{" "}
            <span className="text-emerald-300">{snap.chunks_per_sec_1s}</span>{" "}
            <span className="text-base opacity-50">(목표 ≈50)</span>
          </div>
          <div>
            총 청크 <span className="text-sky-300">{snap.total_chunks.toLocaleString()}</span>
          </div>
          <div>
            총 바이트 <span className="text-sky-300">{fmtBytes(snap.total_bytes)}</span>
          </div>
          <div>
            마지막 청크 후{" "}
            <span className={snap.age_ms !== null && snap.age_ms < 200 ? "text-emerald-300" : "text-amber-300"}>
              {snap.age_ms === null ? "—" : `${snap.age_ms} ms`}
            </span>
          </div>
          <div className="text-xl opacity-60">last seq: {snap.last_seq ?? "—"}</div>
          {snap.stopped_at && (
            <div className="text-amber-300 text-xl">
              stopped at {snap.stopped_at} ({snap.stopped_reason ?? "—"})
            </div>
          )}
        </div>
      )}
    </main>
  );
}
