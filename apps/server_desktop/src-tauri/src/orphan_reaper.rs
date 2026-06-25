// === ANCHOR: ORPHAN_REAPER_START ===
//! Startup orphan reaper.
//!
//! Recurring dev pain: when `tauri:dev` is killed with Ctrl+C the
//! `RunEvent::Exit` handler in `lib.rs` never runs, so the spawned `cloudflared`
//! AND the spawned `yeson-server` survive as orphans from the previous app
//! instance. On the next launch the orphan server holds port 8000 (new start
//! fails `address already in use`) and orphan cloudflared processes keep their
//! trycloudflare quick-tunnel slots (new "Go Live" times out). A server-side
//! parent-death watchdog now lets the server self-exit, but cloudflared is a Go
//! binary that can't, and pre-watchdog servers still orphan — so we want a
//! definitive sweep.
//!
//! This runs ONCE at app startup, BEFORE the operator can start the
//! server/tunnel. At that point there is no legitimate child of ours yet, so any
//! RUNNING process whose executable matches our EXACT absolute vendored binary
//! path is a leftover from a prior run and is reaped. Matching on the absolute
//! path (not the bare name) means an unrelated `cloudflared`/`python`/server
//! elsewhere on the machine is never touched.
//!
//! Best-effort by contract: a missing enumeration tool or zero matches is a
//! clean no-op, our own pid is excluded defensively, and nothing here ever
//! panics or fails app launch. Reaping reuses the proven teardown shapes from
//! `server_process`/`tunnel` (Unix SIGTERM→grace→SIGKILL by pgid; Windows
//! `taskkill /PID <pid> /T /F` subtree).
//!
//! SCOPE: assumes a single app instance. A second CONCURRENT app instance would
//! have its (legitimate) children reaped here — that is out of scope; the app is
//! launched one-at-a-time in practice.
use std::{
    path::Path,
    process::Command,
    thread,
    time::Duration,
};

/// Sweep leftover cloudflared + yeson-server processes from a prior app instance.
/// Call once, early in app startup. Never panics; logs what it reaps via the
/// provided sink.
pub fn reap_orphans(mut log: impl FnMut(&str)) {
    let self_pid = std::process::id();

    if let Some(path) = crate::tunnel::locate_cloudflared() {
        reap_binary(&path, self_pid, "cloudflared", &mut log);
    }
    if let Some(path) = crate::server_process::bundled_server_path() {
        reap_binary(&path, self_pid, "yeson-server", &mut log);
    }
}

/// Reap every RUNNING process launched from `bin_path` (our exact absolute
/// vendored binary), excluding `self_pid`. No matches / missing tool → no-op.
fn reap_binary(bin_path: &Path, self_pid: u32, label: &str, log: &mut impl FnMut(&str)) {
    let abs = match bin_path.canonicalize() {
        Ok(p) => p,
        // If we can't canonicalize our own vendored path the binary effectively
        // isn't there to have spawned anything — nothing to reap.
        Err(_) => return,
    };
    let abs_str = abs.to_string_lossy();

    let pids: Vec<u32> = enumerate_pids(&abs_str)
        .into_iter()
        .filter(|pid| *pid != self_pid)
        .collect();
    if pids.is_empty() {
        return;
    }

    for pid in pids {
        log(&format!(
            "reaping orphan {label} (pid {pid}) from prior app instance: {abs_str}"
        ));
        terminate_pid(pid);
    }
}

// === ANCHOR: ORPHAN_REAPER_ENUMERATE_START ===
/// Enumerate PIDs of running processes whose executable/command line is our
/// absolute binary path. Shelling out is consistent with the existing teardown
/// code (which already shells to `/bin/kill` / `taskkill`). A missing tool or no
/// matches yields an empty list (clean no-op).
#[cfg(unix)]
fn enumerate_pids(abs_path: &str) -> Vec<u32> {
    // `pgrep -f <abs path>` matches against the FULL command line, so a process
    // launched as our absolute path is caught even when args follow. The path is
    // a fixed literal we pass as a single argv element (no shell), so there is no
    // interpolation/regex-injection surface from our own vendored path.
    let output = match Command::new("pgrep").arg("-f").arg(abs_path).output() {
        Ok(o) => o,
        Err(_) => return Vec::new(), // pgrep absent → nothing we can enumerate
    };
    // pgrep exits 1 with empty stdout when there are no matches: that is a clean
    // empty result, not an error.
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| line.trim().parse::<u32>().ok())
        .collect()
}

#[cfg(not(unix))]
fn enumerate_pids(abs_path: &str) -> Vec<u32> {
    // Windows: query the Win32_Process table for rows whose ExecutablePath is our
    // absolute binary, emitting just the PID. `wmic` is the simplest enumerator
    // that exposes the full executable path (tasklist only shows the image name,
    // which wouldn't distinguish OUR vendored copy from an unrelated one).
    // Compare case-insensitively with backslashes normalized, since WMI may
    // report a differently-cased/short-path form than our canonicalized path.
    let want = normalize_win_path(abs_path);
    let output = match Command::new("wmic")
        .args(["process", "get", "ProcessId,ExecutablePath", "/format:csv"])
        .output()
    {
        Ok(o) => o,
        Err(_) => return Vec::new(), // wmic absent → nothing we can enumerate
    };
    let text = String::from_utf8_lossy(&output.stdout);
    let mut pids = Vec::new();
    for line in text.lines() {
        // CSV rows are `Node,ExecutablePath,ProcessId`. Rows without a known exe
        // path (system processes) have an empty ExecutablePath field — skip them.
        let cols: Vec<&str> = line.split(',').collect();
        if cols.len() < 3 {
            continue;
        }
        let exe = cols[cols.len() - 2].trim();
        let pid_field = cols[cols.len() - 1].trim();
        if exe.is_empty() {
            continue;
        }
        if normalize_win_path(exe) == want {
            if let Ok(pid) = pid_field.parse::<u32>() {
                pids.push(pid);
            }
        }
    }
    pids
}

#[cfg(not(unix))]
fn normalize_win_path(path: &str) -> String {
    path.replace('/', "\\").to_ascii_lowercase()
}
// === ANCHOR: ORPHAN_REAPER_ENUMERATE_END ===

// === ANCHOR: ORPHAN_REAPER_TERMINATE_START ===
/// Terminate a single orphan PID robustly, mirroring the existing teardown
/// patterns. Unix: SIGTERM the process group (the orphan was spawned by the
/// prior app as a group leader via `process_group(0)`, so the negative pgid
/// reaps any helper it forked — cloudflared's edge helper, uvicorn worker),
/// short grace, then SIGKILL the group. Windows: `taskkill /PID <pid> /T /F`
/// reaps the whole subtree by PID (the same call `terminate_group` uses).
fn terminate_pid(pid: u32) {
    #[cfg(unix)]
    {
        let pgid = pid as i32;
        let _ = Command::new("/bin/kill")
            .arg("-TERM")
            .arg(format!("-{pgid}"))
            .status();
        // ~1s grace then SIGKILL backstop. We don't own this child's handle (it's
        // from a prior process), so we can't `try_wait()`; the fixed grace +
        // unconditional SIGKILL is the simplest reliable sweep. SIGKILL on an
        // already-dead group is a harmless no-op (kill returns ESRCH).
        thread::sleep(Duration::from_secs(1));
        let _ = Command::new("/bin/kill")
            .arg("-KILL")
            .arg(format!("-{pgid}"))
            .status();
    }
    #[cfg(not(unix))]
    {
        let mut kill = Command::new("taskkill");
        kill.args(["/PID", &pid.to_string(), "/T", "/F"]);
        set_no_window(&mut kill);
        let _ = kill.status();
    }
}

/// Suppress the console window Windows would otherwise pop for the taskkill call.
/// No-op elsewhere. (Same rationale as the teardown helpers in the sibling
/// modules.)
#[cfg(not(unix))]
fn set_no_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    command.creation_flags(CREATE_NO_WINDOW);
}
// === ANCHOR: ORPHAN_REAPER_TERMINATE_END ===

#[cfg(test)]
mod tests {
    use super::*;

    // A path that does not exist canonicalizes to an error → reap_binary returns
    // immediately as a clean no-op (no enumeration, no kills, no panic).
    #[test]
    fn missing_binary_path_is_a_clean_noop() {
        let mut logged = Vec::new();
        reap_binary(
            Path::new("/definitely/not/a/real/vendored/cloudflared"),
            std::process::id(),
            "cloudflared",
            &mut |line| logged.push(line.to_string()),
        );
        assert!(logged.is_empty(), "missing binary must reap nothing");
    }

    // reap_orphans with no orphans present must not panic and (on this dev host,
    // where the app isn't running) reap nothing of note. We just assert it runs
    // to completion without panicking.
    #[test]
    fn reap_orphans_runs_without_panicking() {
        reap_orphans(|_| {});
    }
}
// === ANCHOR: ORPHAN_REAPER_END ===
