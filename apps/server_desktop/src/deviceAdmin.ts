// === ANCHOR: DEVICE_ADMIN_START ===
// Device-key admin for the server console (security: key issue/revoke belongs on
// the SERVER control plane, not on client machines). Talks to the bundled
// server's loopback REST (127.0.0.1:<port>) — the same /api/v1/devices endpoints
// the client app used, but here the admin token never leaves the operator's own
// server machine. The token is held only in the console's in-memory state for the
// session; the durable device key is returned ONCE on issue and shown once.
const API = "/api/v1";

export type DeviceOut = {
  id: number;
  name: string;
  is_active: boolean;
  created_at: string;
};

export type NewDevice = { id: number; name: string; api_key: string };

function base(port: number): string {
  return `http://127.0.0.1:${port}`;
}

/** Operator login → admin access token (held in console memory for the session). */
export async function login(port: number, email: string, password: string): Promise<string> {
  const r = await fetch(`${base(port)}${API}/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!r.ok) {
    throw new Error(
      r.status === 401 ? "이메일 또는 비밀번호가 올바르지 않습니다" : `로그인 실패 (HTTP ${r.status})`,
    );
  }
  // Intentionally keep ONLY the short-lived access token; the refresh token is
  // dropped (no long-lived credential sitting in webview memory). On access-token
  // expiry the panel's 401 path drops the session and the operator re-logs in.
  const data = (await r.json()) as { access_token: string };
  return data.access_token;
}

export async function listDevices(port: number, token: string): Promise<DeviceOut[]> {
  const r = await fetch(`${base(port)}${API}/devices`, {
    headers: { authorization: `Bearer ${token}` },
  });
  if (r.status === 401) throw new Error("세션이 만료되었습니다 — 다시 로그인하세요");
  if (!r.ok) throw new Error(`디바이스 목록 조회 실패 (HTTP ${r.status})`);
  return (await r.json()) as DeviceOut[];
}

/** Issue a new device key. The plaintext key is returned ONCE (server stores only the hash). */
export async function createDevice(port: number, token: string, name: string): Promise<NewDevice> {
  const r = await fetch(`${base(port)}${API}/devices`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: JSON.stringify({ name }),
  });
  if (r.status === 401) throw new Error("세션이 만료되었습니다 — 다시 로그인하세요");
  if (!r.ok) throw new Error(`디바이스 키 발급 실패 (HTTP ${r.status})`);
  return (await r.json()) as NewDevice;
}

export async function revokeDevice(port: number, token: string, id: number): Promise<void> {
  const r = await fetch(`${base(port)}${API}/devices/${id}/revoke`, {
    method: "POST",
    headers: { authorization: `Bearer ${token}` },
  });
  if (r.status === 401) throw new Error("세션이 만료되었습니다 — 다시 로그인하세요");
  if (!r.ok && r.status !== 204) throw new Error(`디바이스 키 폐기 실패 (HTTP ${r.status})`);
}
// === ANCHOR: DEVICE_ADMIN_END ===
