// === ANCHOR: BACKUP_PANEL_START ===
// Server-console backup panel (S1/S2). Operator logs in once per session and
// manages a LIST of destinations — a cloud-sync folder (Google Drive/OneDrive)
// AND a LAN NAS path are both kept, so one run fans an off-machine copy to each.
// A backup is a transactionally-consistent SQLite snapshot (VACUUM INTO) + a zip
// of the storage/ artifact tree, integrity-checked server-side, then pruned to
// `keep` most-recent backups per destination. A destination that is offline
// (unmounted NAS / typo) fails in isolation — the others still succeed.
// Destinations + keep persist in localStorage (a console-side cache); auto
// triggers (meeting end + daily) move authority server-side in S3.
import type { CSSProperties } from "react";
import { useCallback, useEffect, useState } from "react";

import { type BackupRunResult, login, pickBackupDir, runBackup } from "./backupAdmin";

type Props = { serverPort: number | null; running: boolean };

const DESTS_KEY = "yeson.backup.dests";
const KEEP_KEY = "yeson.backup.keep";
const DEFAULT_KEEP = 14;

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function loadDests(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(DESTS_KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function loadKeep(): number {
  const n = Number(localStorage.getItem(KEEP_KEY));
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : DEFAULT_KEEP;
}

export default function BackupPanel({ serverPort, running }: Props) {
  const [token, setToken] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [dests, setDests] = useState<string[]>(loadDests);
  const [keep, setKeep] = useState<number>(loadKeep);
  const [manual, setManual] = useState("");
  const [result, setResult] = useState<BackupRunResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    localStorage.setItem(DESTS_KEY, JSON.stringify(dests));
  }, [dests]);
  useEffect(() => {
    localStorage.setItem(KEEP_KEY, String(keep));
  }, [keep]);

  const addDest = useCallback((path: string) => {
    const p = path.trim();
    if (!p) return;
    setDests((prev) => (prev.includes(p) ? prev : [...prev, p]));
  }, []);

  const onLogin = useCallback(async () => {
    if (serverPort == null) return;
    setBusy(true);
    setError(null);
    try {
      setToken(await login(serverPort, email.trim(), password));
      setPassword("");
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }, [serverPort, email, password]);

  const onPick = useCallback(async () => {
    setError(null);
    try {
      const picked = await pickBackupDir();
      if (picked) addDest(picked);
    } catch (e) {
      setError(errText(e));
    }
  }, [addDest]);

  const onBackup = useCallback(async () => {
    if (serverPort == null || token == null) return;
    if (dests.length === 0) {
      setError("백업 대상 폴더를 하나 이상 추가하세요");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setResult(await runBackup(serverPort, token, dests, keep));
    } catch (e) {
      setError(errText(e));
      if (errText(e).includes("세션이 만료")) setToken(null);
    } finally {
      setBusy(false);
    }
  }, [serverPort, token, dests, keep]);

  if (!running || serverPort == null) {
    return (
      <div style={s.wrap}>
        <p style={s.hint}>서버를 먼저 시작하세요 — 백업은 실행 중인 서버에 연결합니다.</p>
      </div>
    );
  }

  if (token == null) {
    return (
      <div style={s.wrap}>
        <h2 style={s.title}>회의록 백업 — 운영자 로그인</h2>
        <p style={s.hint}>이 서버를 부트스트랩할 때 만든 운영자 계정으로 로그인하세요.</p>
        <div style={s.row}>
          <input style={s.input} type="email" placeholder="이메일" value={email} onChange={(e) => setEmail(e.target.value)} />
          <input
            style={s.input}
            type="password"
            placeholder="비밀번호"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !busy && email && password) void onLogin();
            }}
          />
          <button style={s.primary} onClick={() => void onLogin()} disabled={busy || !email || !password}>
            로그인
          </button>
        </div>
        {error ? <p style={s.error}>{error}</p> : null}
      </div>
    );
  }

  const resultByDir = new Map((result?.destinations ?? []).map((d) => [d.dest_dir, d]));

  return (
    <div style={s.wrap}>
      <div style={s.headRow}>
        <h2 style={s.title}>회의록 백업</h2>
        <button style={s.muted} onClick={() => setToken(null)}>로그아웃</button>
      </div>
      <p style={s.hint}>
        DB 스냅샷(일관성 보장 VACUUM INTO)과 보고서/익스포트(storage) 압축본을 아래 모든 폴더에 저장합니다.
        클라우드 동기화 폴더와 LAN NAS 경로를 함께 추가하면 한 번에 오프머신 사본까지 보관됩니다.
        오프라인 폴더(미마운트 NAS 등)는 해당 폴더만 실패하고 나머지는 정상 저장됩니다.
      </p>

      {/* Destinations */}
      <div style={s.listLabel}>대상 폴더 {dests.length}개</div>
      {dests.length === 0 ? (
        <p style={s.hint}>대상 폴더가 없습니다. 아래에서 추가하세요.</p>
      ) : (
        dests.map((d) => {
          const dr = resultByDir.get(d);
          return (
            <div key={d} style={s.destRow}>
              <code style={s.destPath}>{d}</code>
              {dr ? (
                dr.ok ? (
                  <span style={s.okTag}>✓ 저장됨{dr.pruned > 0 ? ` · ${dr.pruned}개 정리` : ""}</span>
                ) : (
                  <span style={s.failTag} title={dr.error ?? ""}>✕ 실패</span>
                )
              ) : null}
              <button style={s.removeBtn} onClick={() => setDests((p) => p.filter((x) => x !== d))} disabled={busy}>
                제거
              </button>
            </div>
          );
        })
      )}

      {/* Add destination: native picker OR manual path */}
      <div style={s.row}>
        <button style={s.muted} onClick={() => void onPick()} disabled={busy}>📁 폴더 선택…</button>
        <input
          style={{ ...s.input, flex: "1 1 280px", fontFamily: "var(--ys-font-mono)" }}
          placeholder="또는 경로 직접 입력 후 추가"
          value={manual}
          onChange={(e) => setManual(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              addDest(manual);
              setManual("");
            }
          }}
        />
        <button
          style={s.muted}
          onClick={() => {
            addDest(manual);
            setManual("");
          }}
          disabled={busy || !manual.trim()}
        >
          추가
        </button>
      </div>

      {/* Retention + run */}
      <div style={s.row}>
        <label style={s.keepLabel}>
          보관 개수
          <input
            style={{ ...s.input, width: 64, marginLeft: 8 }}
            type="number"
            min={1}
            value={keep}
            onChange={(e) => setKeep(Math.max(1, Math.floor(Number(e.target.value) || DEFAULT_KEEP)))}
          />
        </label>
        <span style={s.hint}>폴더별 최근 {keep}개만 보관(오래된 백업 자동 삭제)</span>
        <button style={s.primary} onClick={() => void onBackup()} disabled={busy || dests.length === 0}>
          {busy ? "백업 중…" : "지금 백업"}
        </button>
      </div>

      {error ? <p style={s.error}>{error}</p> : null}

      {result ? (
        <div style={s.resultBox} role="status">
          <div style={{ fontWeight: 700 }}>
            {result.destinations.every((d) => d.ok) ? "✓ 백업 완료" : "⚠️ 일부 폴더 실패"} — {result.stamp}
            {result.integrity_ok ? " · 무결성 검증 통과" : " · ⚠️ 무결성 검증 실패"} · DB {fmtBytes(result.snapshot_bytes)}
          </div>
          {result.destinations.filter((d) => !d.ok).map((d) => (
            <div key={d.dest_dir} style={s.failDetail}>✕ {d.dest_dir} — {d.error}</div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  wrap: { padding: "14px 20px", display: "flex", flexDirection: "column", gap: 10 },
  headRow: { display: "flex", alignItems: "center", justifyContent: "space-between" },
  title: { fontSize: 15, fontWeight: 600, margin: 0, color: "var(--ys-text-strong)" },
  hint: { fontSize: 13, color: "var(--ys-text-faint)", margin: 0, lineHeight: 1.5 },
  row: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  input: { padding: "6px 10px", background: "var(--ys-bg-app)", border: "1px solid var(--ys-border-strong)", borderRadius: "var(--ys-radius-control)", color: "var(--ys-text-body)", fontSize: 13 },
  primary: { padding: "6px 14px", borderRadius: "var(--ys-radius-control)", border: "1px solid var(--ys-accent-strong)", background: "var(--ys-accent-strong)", color: "var(--ys-on-accent)", fontWeight: 600, cursor: "pointer", fontSize: 13 },
  muted: { padding: "6px 12px", borderRadius: "var(--ys-radius-control)", border: "1px solid var(--ys-border-strong)", background: "transparent", color: "var(--ys-text-label)", cursor: "pointer", fontSize: 13 },
  removeBtn: { padding: "4px 10px", borderRadius: "var(--ys-radius-control)", border: "1px solid var(--ys-border-strong)", background: "transparent", color: "var(--ys-text-faint)", cursor: "pointer", fontSize: 12 },
  error: { color: "var(--ys-danger-text)", fontSize: 13, margin: "4px 0 0" },
  listLabel: { fontSize: 12, textTransform: "uppercase", letterSpacing: 0.5, color: "var(--ys-text-muted)", marginTop: 4 },
  destRow: { display: "flex", alignItems: "center", gap: 10, padding: "5px 0", borderBottom: "1px solid var(--ys-border-subtle)" },
  destPath: { fontFamily: "var(--ys-font-mono)", fontSize: 12, wordBreak: "break-all", color: "var(--ys-text-strong)", flex: "1 1 auto" },
  okTag: { fontSize: 12, color: "var(--ys-success-text)", whiteSpace: "nowrap" },
  failTag: { fontSize: 12, color: "var(--ys-danger-text)", whiteSpace: "nowrap" },
  keepLabel: { fontSize: 13, color: "var(--ys-text-label)", display: "flex", alignItems: "center" },
  resultBox: { padding: "10px 14px", borderRadius: "var(--ys-radius-md)", border: "1px solid var(--ys-success-border)", background: "var(--ys-success-bg)", color: "var(--ys-success-text)", fontSize: 13, display: "flex", flexDirection: "column", gap: 4 },
  failDetail: { fontSize: 12, color: "var(--ys-danger-text)", wordBreak: "break-all" },
};
// === ANCHOR: BACKUP_PANEL_END ===
