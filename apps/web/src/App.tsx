// === ANCHOR: APP_START ===
import { SubtitleView } from "./components/SubtitleView";
import { AdminAudioStats } from "./components/AdminAudioStats";

function parseViewerToken(): string | null {
  const m = window.location.pathname.match(/^\/v\/([A-Za-z0-9_-]+)\/?$/);
  return m ? (m[1] ?? null) : null;
}

function parseAdminAudioStats(): { sessionId: string; token: string } | null {
  if (window.location.pathname !== "/admin/audio-stats") return null;
  const params = new URLSearchParams(window.location.search);
  const sessionId = params.get("session");
  const token = params.get("token");
  if (!sessionId || !token) return null;
  return { sessionId, token };
}

export default function App() {
  const token = parseViewerToken();
  if (token) return <SubtitleView token={token} />;

  const admin = parseAdminAudioStats();
  if (admin) return <AdminAudioStats sessionId={admin.sessionId} token={admin.token} />;

  return (
    <main className="min-h-screen bg-slate-900 text-slate-100 flex items-center justify-center">
      <h1 className="text-4xl font-bold">Hello yeson-meet</h1>
    </main>
  );
}
// === ANCHOR: APP_END ===
