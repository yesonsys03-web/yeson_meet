// === ANCHOR: DEVICE_PANEL_START ===
// Server-console device-key admin panel (security: issue/revoke device keys on
// the server control plane, so client machines never hold an admin login).
// Operator logs in once per session → list / issue / revoke. A freshly issued
// key is shown ONCE (the server stores only its hash) with a copy button and a
// "won't be shown again" warning — it is the durable bearer the client enters.
import type { CSSProperties } from "react";
import { useCallback, useEffect, useState } from "react";

import { createDevice, type DeviceOut, listDevices, login, type NewDevice, revokeDevice } from "./deviceAdmin";

type Props = { serverPort: number | null; running: boolean };

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default function DevicePanel({ serverPort, running }: Props) {
  const [token, setToken] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [devices, setDevices] = useState<DeviceOut[]>([]);
  const [newName, setNewName] = useState("sidecar");
  const [issued, setIssued] = useState<NewDevice | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (serverPort == null || token == null) return;
    try {
      setDevices(await listDevices(serverPort, token));
    } catch (e) {
      setError(errText(e));
      if (errText(e).includes("세션이 만료")) setToken(null);
    }
  }, [serverPort, token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onLogin = useCallback(async () => {
    if (serverPort == null) return;
    setBusy(true);
    setError(null);
    try {
      setToken(await login(serverPort, email.trim(), password));
      setPassword(""); // drop the password from state once exchanged for a token
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }, [serverPort, email, password]);

  // Copy the key to the clipboard and mark it copied (enables "닫기"). Used for
  // both the manual Copy button (always a user gesture → reliable) and a
  // best-effort auto-copy right after issue (post-await may be blocked by the
  // webview with no active gesture — then `copied` stays false and the manual
  // button + disabled-close fallback keeps the key from being lost).
  const copyKey = useCallback(async (key: string) => {
    try {
      await navigator.clipboard.writeText(key);
      setCopied(true);
    } catch {
      /* clipboard unavailable/blocked — operator copies manually */
    }
  }, []);

  const onIssue = useCallback(async () => {
    if (serverPort == null || token == null) return;
    setBusy(true);
    setError(null);
    try {
      const device = await createDevice(serverPort, token, newName.trim() || "sidecar");
      setCopied(false);
      setIssued(device);
      void copyKey(device.api_key); // best-effort auto-copy
      await refresh();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }, [serverPort, token, newName, refresh, copyKey]);

  const onRevoke = useCallback(
    async (id: number) => {
      if (serverPort == null || token == null) return;
      setBusy(true);
      setError(null);
      try {
        await revokeDevice(serverPort, token, id);
        await refresh();
      } catch (e) {
        setError(errText(e));
      } finally {
        setBusy(false);
      }
    },
    [serverPort, token, refresh],
  );

  if (!running || serverPort == null) {
    return <div style={s.wrap}><p style={s.hint}>서버를 먼저 시작하세요 — 디바이스 키 관리는 실행 중인 서버에 연결합니다.</p></div>;
  }

  if (token == null) {
    return (
      <div style={s.wrap}>
        <h2 style={s.title}>디바이스 키 관리 — 운영자 로그인</h2>
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

  const active = devices.filter((d) => d.is_active);
  const inactive = devices.filter((d) => !d.is_active);

  return (
    <div style={s.wrap}>
      <div style={s.headRow}>
        <h2 style={s.title}>디바이스 키 관리</h2>
        <button style={s.muted} onClick={() => setToken(null)}>로그아웃</button>
      </div>

      {/* Issue */}
      <div style={s.row}>
        <input style={s.input} placeholder="이름 (예: sidecar)" value={newName} onChange={(e) => setNewName(e.target.value)} />
        <button style={s.primary} onClick={() => void onIssue()} disabled={busy}>새 키 발급</button>
        <button style={s.muted} onClick={() => void refresh()} disabled={busy}>새로고침</button>
      </div>

      {/* One-time key display */}
      {issued ? (
        <div style={s.keyBox} role="alert">
          <div style={{ fontWeight: 700, marginBottom: 4 }}>새 디바이스 키 (#{issued.id} · {issued.name}) — 다시 표시되지 않습니다. 복사 후 닫으세요.</div>
          <code style={s.keyCode}>{issued.api_key}</code>
          <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
            <button style={s.primary} onClick={() => void copyKey(issued.api_key)}>
              {copied ? "복사됨 ✓" : "복사"}
            </button>
            <button
              style={{ ...s.muted, ...(copied ? {} : { opacity: 0.5, cursor: "not-allowed" }) }}
              onClick={() => setIssued(null)}
              disabled={!copied}
              title={copied ? "" : "먼저 키를 복사하세요"}
            >
              닫기
            </button>
            {copied ? (
              <span style={{ fontSize: 12, color: "#4ade80" }}>클립보드에 복사됨 — 이제 닫아도 됩니다</span>
            ) : (
              <span style={{ fontSize: 12, color: "#ffd27a" }}>복사하면 닫기가 활성화됩니다</span>
            )}
          </div>
          <div style={s.warn}>⚠️ 만료 없는 베어러 키입니다 — 비밀번호처럼 다루고, 노출되면 폐기 후 재발급하세요.</div>
        </div>
      ) : null}

      {error ? <p style={s.error}>{error}</p> : null}

      {/* Active devices */}
      <div style={s.listLabel}>활성 키 {active.length}개</div>
      {active.length === 0 ? (
        <p style={s.hint}>활성 디바이스 키가 없습니다. 위에서 새로 발급하세요.</p>
      ) : (
        active.map((d) => (
          <div key={d.id} style={s.deviceRow}>
            <span style={s.deviceName}>#{d.id} · {d.name}</span>
            <span style={s.deviceMeta}>{new Date(d.created_at).toLocaleString()}</span>
            <button style={s.danger} onClick={() => void onRevoke(d.id)} disabled={busy}>폐기</button>
          </div>
        ))
      )}

      {/* Inactive (revoked) — read-only history, no revoke action */}
      {inactive.length > 0 ? (
        <>
          <div style={{ ...s.listLabel, color: "#6b7c8d" }}>비활성(폐기됨) {inactive.length}개</div>
          {inactive.map((d) => (
            <div key={d.id} style={{ ...s.deviceRow, opacity: 0.55 }}>
              <span style={s.deviceName}>#{d.id} · {d.name}</span>
              <span style={s.deviceMeta}>{new Date(d.created_at).toLocaleString()}</span>
              <span style={{ fontSize: 12, color: "#6b7c8d" }}>폐기됨</span>
            </div>
          ))}
        </>
      ) : null}
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  wrap: { padding: "14px 20px", display: "flex", flexDirection: "column", gap: 10 },
  headRow: { display: "flex", alignItems: "center", justifyContent: "space-between" },
  title: { fontSize: 15, fontWeight: 600, margin: 0 },
  hint: { fontSize: 13, color: "#6b7c8d", margin: 0 },
  row: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  input: { padding: "6px 10px", background: "#0b1117", border: "1px solid #25323f", borderRadius: 6, color: "#d4dde6", fontSize: 13 },
  primary: { padding: "6px 14px", borderRadius: 6, border: "1px solid #15803d", background: "#15803d", color: "#fff", fontWeight: 600, cursor: "pointer", fontSize: 13 },
  muted: { padding: "6px 12px", borderRadius: 6, border: "1px solid #25323f", background: "#1b2530", color: "#d4dde6", cursor: "pointer", fontSize: 13 },
  danger: { padding: "5px 12px", borderRadius: 6, border: "1px solid #b91c1c", background: "#b91c1c", color: "#fff", fontWeight: 600, cursor: "pointer", fontSize: 12 },
  keyBox: { padding: "10px 14px", borderRadius: 8, border: "1px solid #d98a00", background: "#3a2c08", color: "#ffd27a", fontSize: 13 },
  keyCode: { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 13, wordBreak: "break-all", color: "#fff" },
  warn: { fontSize: 12, marginTop: 6, opacity: 0.9 },
  error: { color: "#ff6b6b", fontSize: 13, margin: "4px 0 0" },
  listLabel: { fontSize: 12, textTransform: "uppercase", letterSpacing: 0.5, color: "#8ea0b2", marginTop: 6 },
  deviceRow: { display: "flex", alignItems: "center", gap: 12, padding: "6px 0", borderBottom: "1px solid #1d2733" },
  deviceName: { fontSize: 13, fontWeight: 600, minWidth: 160 },
  deviceMeta: { fontSize: 12, color: "#6b7c8d", flex: "1 1 auto" },
};
// === ANCHOR: DEVICE_PANEL_END ===
