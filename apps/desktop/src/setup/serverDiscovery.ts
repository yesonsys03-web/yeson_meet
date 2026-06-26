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

export async function resolveServerWsBase(deps: ResolveDeps): Promise<string | null> {
  if (await deps.probeLocal()) return LOCAL_WS_BASE;
  const found = await deps.discover();
  return found ? wsBaseFromDiscovery(found) : null;
}
