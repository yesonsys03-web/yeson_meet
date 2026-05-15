// === ANCHOR: APP_START ===
import { SubtitleView } from "./components/SubtitleView";

function parseViewerToken(): string | null {
  const m = window.location.pathname.match(/^\/v\/([A-Za-z0-9_-]+)\/?$/);
  return m ? (m[1] ?? null) : null;
}

export default function App() {
  const token = parseViewerToken();
  if (token) return <SubtitleView token={token} />;
  return (
    <main className="min-h-screen bg-slate-900 text-slate-100 flex items-center justify-center">
      <h1 className="text-4xl font-bold">Hello yeson-meet</h1>
    </main>
  );
}
// === ANCHOR: APP_END ===
