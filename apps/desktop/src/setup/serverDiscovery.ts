import { invoke } from "@tauri-apps/api/core";

const LOCAL_WS_BASE = "ws://127.0.0.1:8000";
const LOCAL_HEALTH_URL = "http://127.0.0.1:8000/api/v1/health";

export type DiscoveredServer = { ip: string; port: number };

export type ResolveDeps = {
  probeLocal: () => Promise<boolean>;
  discover: () => Promise<DiscoveredServer | null>;
};

export function wsBaseFromDiscovery(found: DiscoveredServer): string {
  return `ws://${found.ip}:${found.port}`;
}

export async function probeLocalServer(fetchImpl: typeof fetch = fetch): Promise<boolean> {
  try {
    const response = await fetchImpl(LOCAL_HEALTH_URL);
    return response.ok;
  } catch {
    return false;
  }
}

export async function discoverServer(): Promise<DiscoveredServer | null> {
  try {
    return (await invoke<DiscoveredServer | null>("discover_server")) ?? null;
  } catch {
    return null;
  }
}

/** Normalises free-form user input into a ws(s):// URL.
 *  bare IP/host       → ws://<host>:8000
 *  host:port          → ws://<host>:<port>
 *  ws:// or wss://    → returned unchanged
 *  empty / whitespace → ""
 */
export function normalizeServerWsBase(input: string): string {
  const v = input.trim();
  if (!v) return "";
  if (/^wss?:\/\//i.test(v)) return v;          // already a ws/wss URL → leave as-is
  const hasPort = /:\d+$/.test(v);              // host or host:port (bare IP/hostname)
  return `ws://${v}${hasPort ? "" : ":8000"}`;
}

export async function resolveServerWsBase(deps: ResolveDeps): Promise<string | null> {
  if (await deps.probeLocal()) return LOCAL_WS_BASE;
  const found = await deps.discover();
  return found ? wsBaseFromDiscovery(found) : null;
}
