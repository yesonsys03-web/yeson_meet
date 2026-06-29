// === ANCHOR: BACKUP_RESTORE_START ===
// Helpers for the restore section of the Backup panel. `pairBackups` is pure
// logic and unit-testable without a running app; the remaining exports
// (listDir, inspectBackup, restoreBackup) wrap Tauri `invoke` commands.

import { invoke } from "@tauri-apps/api/core";

export type BackupPair = {
  stamp: string;
  snapshot: string;      // filename only, e.g. "yeson-meet-20260629-120000.db"
  storageZip: string | null; // filename only, or null when absent
};

/**
 * Given a flat list of filenames inside a backup folder, return the
 * stamp-paired (snapshot, optional storage zip) candidates, newest first.
 */
export function pairBackups(files: string[]): BackupPair[] {
  const snaps = files.filter((f) => /^yeson-meet-\d{8}-\d{6}\.db$/.test(f));
  const zips = new Set(files.filter((f) => /^storage-\d{8}-\d{6}\.zip$/.test(f)));
  return snaps
    .map((s) => {
      const stamp = s.slice("yeson-meet-".length, -".db".length);
      const zip = `storage-${stamp}.zip`;
      return { stamp, snapshot: s, storageZip: zips.has(zip) ? zip : null };
    })
    .sort((a, b) => (a.stamp < b.stamp ? 1 : -1)); // newest first
}

/** List the filenames (not full paths) inside `dir` via a Rust command. */
export async function listDir(dir: string): Promise<string[]> {
  return invoke<string[]>("list_dir", { path: dir });
}

export type InspectResult = {
  stamp: string;
  integrity_ok: boolean;
  app_version: string | null;
  session_count: number;
  utterance_count: number;
  snapshot_bytes: number;
  has_storage_zip: boolean;
  validation: { ok: boolean; level: "ok" | "warn" | "block"; reason: string };
};

/** Call the Tauri inspect_backup command. snapshotPath is the full path. */
export async function inspectBackup(snapshotPath: string): Promise<InspectResult> {
  return invoke<InspectResult>("inspect_backup", { snapshotPath });
}

export type RestoreResult = {
  integrity_ok: boolean;
  restored_bytes: number;
  storage_restored: boolean;
  safety_dir: string;
};

/** Call the Tauri restore_backup command. Paths are full paths. */
export async function restoreBackup(
  snapshotPath: string,
  storageZipPath: string | null,
): Promise<RestoreResult> {
  return invoke<RestoreResult>("restore_backup", {
    snapshotPath,
    storageZipPath: storageZipPath ?? null,
  });
}
// === ANCHOR: BACKUP_RESTORE_END ===
