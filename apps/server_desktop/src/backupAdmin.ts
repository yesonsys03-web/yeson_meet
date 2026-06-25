// === ANCHOR: BACKUP_ADMIN_START ===
// Meeting-record backup admin for the server console (S1/S2). Talks to the
// bundled server's loopback REST (127.0.0.1:<port>) /api/v1/backup/run —
// operator-gated, so the trigger lives on the server control plane like
// device-key admin. The operator login + access-token handling is shared with
// `deviceAdmin` (same short-lived in-memory token, dropped on 401 / re-login).
// The native folder picker is a plugin-free Rust command (`pick_backup_dir`).
import { invoke } from "@tauri-apps/api/core";

import { login } from "./deviceAdmin";

export { login };

const API = "/api/v1";

export type DestinationResult = {
  dest_dir: string;
  ok: boolean;
  snapshot_path: string | null;
  storage_zip_path: string | null;
  pruned: number;
  error: string | null;
};

export type BackupRunResult = {
  stamp: string;
  snapshot_bytes: number;
  integrity_ok: boolean;
  destinations: DestinationResult[];
};

function base(port: number): string {
  return `http://127.0.0.1:${port}`;
}

/** Open the native folder picker; resolves to the chosen path or null (cancel). */
export async function pickBackupDir(): Promise<string | null> {
  return (await invoke<string | null>("pick_backup_dir")) ?? null;
}

/**
 * Run one backup now: a verified DB snapshot + storage zip is written into every
 * destination, each pruned to `keep` most-recent backups. Per-destination
 * failures (e.g. an unmounted NAS) come back as ok=false entries, not a throw.
 */
export async function runBackup(
  port: number,
  token: string,
  destDirs: string[],
  keep: number,
): Promise<BackupRunResult> {
  const r = await fetch(`${base(port)}${API}/backup/run`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: JSON.stringify({ dest_dirs: destDirs, keep }),
  });
  if (r.status === 401) throw new Error("세션이 만료되었습니다 — 다시 로그인하세요");
  if (r.status === 400) {
    const detail = await r.json().catch(() => null);
    throw new Error(
      typeof detail?.detail === "string"
        ? `백업 실패: ${detail.detail}`
        : "백업 실패 — 대상 폴더를 하나 이상 추가하세요",
    );
  }
  if (!r.ok) throw new Error(`백업 실패 (HTTP ${r.status})`);
  return (await r.json()) as BackupRunResult;
}
// === ANCHOR: BACKUP_ADMIN_END ===
