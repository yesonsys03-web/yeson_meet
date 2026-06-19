# P0 — Windows freeze + Job-Object teardown spike (runbook)

> **What this is:** the turnkey program's #1 pre-mortem risk (PM-1) and highest *irreversible* unknown — does the frozen server bundle boot on Windows, and does closing the console reap the whole server subtree (no orphan keeping the port bound / SQLite locked)? It gates packaging on Linux+Windows and could force an architecture change (uv-launcher).
> **Measure-first:** macOS teardown is proven (process-group kill). Windows is **unproven**. We do NOT assume a Job Object is needed — we **measure** whether the current teardown orphans anything, then add the Job Object **only if** it does.
> **Run on a real Windows 10/11 x64 machine.** macOS/Linux cannot build or test the Windows freeze (no cross-compile; `cfg(windows)` Rust isn't compiled off-Windows).

---

## The exact gap being tested

`apps/server_desktop/src-tauri/src/server_process.rs::terminate_group` reaps the
server by signalling the **process group** on Unix, but on Windows falls back to
just `child.kill()`:

```rust
#[cfg(not(unix))]
{
    let _ = child.kill();   // TerminateProcess on the TOP handle only
}
```

Its own doc-comment (lines 48–52) names the risk: a bare `child.kill()` "would
SIGKILL only the launcher and orphan uvicorn, which would keep the port bound and
the SQLite file locked." On Unix the process-group kill closes that gap. **On
Windows there is no group** — the parallel mechanism is a **Job Object** with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (the same approach the client already proved
in `apps/client_sidecar/audio/sources/win_job_object.py`).

**But** a PyInstaller `--onedir` server running `uvicorn.run()` in-process may be
a **single** process (no `--reload`, no `workers>1`), in which case `child.kill()`
already reaps everything and **no Job Object is needed**. The spike decides which
world we're in, cheaply, before any dependent P1–P4 work.

---

## Prerequisites (Windows host)

- [uv](https://docs.astral.sh/uv) + Python 3.12, `pnpm` + Node, Visual C++ runtime.
- PowerShell 7 (`pwsh`) recommended (Windows PowerShell 5.1 also works).
- Rust toolchain only if you reach the Job-Object step (rebuild the Tauri shell).

---

## Steps

### 1. Freeze the server (P0.1 build)
```powershell
pwsh apps/server_desktop/scripts/build-server.ps1
```
Produces `apps/server_desktop/src-tauri/binaries/yeson-server-x86_64-pc-windows-msvc/yeson-server.exe`
(+ `_internal/`) and vendors `cloudflared.exe`.

### 2. Run the measurement harness (P0.1 / P0.2 / P0.3)
```powershell
pwsh apps/server_desktop/scripts/win-freeze-spike.ps1
```
It uses an **isolated temp DB** under `%TEMP%\yeson-p0-spike` and prints PASS/FAIL
per AC plus a verdict. What it asserts:

| AC | Assertion | How |
|----|-----------|-----|
| **P0.1** | frozen onedir boots; `create_schema` writes tables; `/health` → 200 | boot the exe, poll `/api/v1/health`, check the cold `.db` is non-empty |
| **P0.2** | killing **only the top PID** (mimics `child.kill()`) leaves **no orphan** | `Stop-Process -Id <pid>` then assert zero residual `yeson-server.exe`/`python.exe` from the spike |
| **P0.3** | relaunch against the **same DB** boots clean — port free, **no `database is locked`** | re-boot, poll `/health`, grep logs (NOT "exit 0": uvicorn SIGTERM is 143 by design) |

### 3. Read the verdict
- **All PASS** → Windows freeze + the current `child.kill()` teardown are sufficient. **No Rust change.** The P0 unknown is retired; proceed to P1.
- **P0.2 FAIL (orphan survives)** → apply the **Job Object fix** below, rebuild the shell, re-run the harness. Expect P0.2/P0.3 to flip to PASS.
- **P0.1 FAIL (freeze won't boot)** → trigger the **P0.4 uv-launcher contingency** (below) and record it **before** any P1–P4 work.

---

## Job Object fix — apply ONLY if P0.2 fails

> ⚠️ This Rust is `cfg(windows)`-gated and **cannot be compile-checked on macOS/Linux** — apply and `cargo build` it **on Windows**. `windows-sys 0.61` is already in the resolved dependency tree (transitive), so this only promotes it to a direct, Windows-only dep.

**1) `apps/server_desktop/src-tauri/Cargo.toml`** — add a Windows-only dep:
```toml
[target.'cfg(windows)'.dependencies]
windows-sys = { version = "0.61", features = [
    "Win32_Foundation",
    "Win32_System_JobObjects",
] }
```

**2) `server_process.rs`** — hold a job handle on the running server and let
`KILL_ON_JOB_CLOSE` reap the tree. Sketch (adapt to the existing structs):

```rust
// RunningServer gains a Windows-only job handle. Closing the LAST handle to a
// job with KILL_ON_JOB_CLOSE terminates every process still assigned to it.
struct RunningServer {
    child: Child,
    port: u16,
    started_at: Instant,
    #[cfg(windows)]
    job: Option<JobHandle>,   // see wrapper below
}

#[cfg(windows)]
struct JobHandle(isize);
#[cfg(windows)]
unsafe impl Send for JobHandle {}
#[cfg(windows)]
impl Drop for JobHandle {
    fn drop(&mut self) {
        // CloseHandle on the last ref triggers KILL_ON_JOB_CLOSE -> whole tree dies.
        unsafe { windows_sys::Win32::Foundation::CloseHandle(self.0 as _); }
    }
}

/// Create a kill-on-close job and assign the freshly-spawned child to it. Call
/// immediately after `command.spawn()` (the assign window before uvicorn could
/// fork is negligible; CREATE_SUSPENDED + ResumeThread would close it fully but
/// needs the main-thread handle std::process doesn't expose).
#[cfg(windows)]
fn assign_to_kill_on_close_job(child: &Child) -> Option<JobHandle> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, SetInformationJobObject,
        JobObjectExtendedLimitInformation, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() { return None; }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const _,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        AssignProcessToJobObject(job, child.as_raw_handle() as _);
        Some(JobHandle(job as isize))
    }
}
```

- In `start_server_inner`, after `command.spawn()`: `#[cfg(windows)] let job = assign_to_kill_on_close_job(&child);` and store it in `RunningServer`.
- In `terminate_group`, the Windows branch keeps `child.kill()` as a fast path, but the real reaper is dropping `RunningServer.job` (CloseHandle → kill-on-close). Ensure the job is dropped on every teardown path (`stop_server_inner`, `shutdown`, `Drop`).
- Nested jobs (the Tauri app may itself be in a job) are supported on Windows 8+; no action needed.

**3) Re-run** `win-freeze-spike.ps1` — but note it tests the *raw* `child.kill()`
contract. To validate the Job Object end-to-end, test through the actual Tauri
console: Start the server, close the console window, then `tasklist` must show
zero residual `yeson-server.exe`/`python.exe` (P0.2), and a relaunch must boot
clean (P0.3).

---

## P0.4 — uv-launcher contingency (only if the freeze itself fails)

If **P0.1 fails** — PyInstaller can't bundle the server on Windows (grpc/genai
native deps, or it won't boot) — do **not** sink more time into the freeze.
Switch the Windows server to a **uv-launcher**: ship the `apps/server` source +
a pinned `uv` and launch `uv run python -m apps.server_desktop.sidecar.server_entry`
from the Tauri shell instead of a frozen exe. This trades a heavier first-run
(uv resolves/install on first launch) for avoiding the freeze entirely.

**Record this branch decision before starting any P1–P4 work** (it changes the
packaging assumption all later phases build on). Capture: which AC failed, the
PyInstaller error, and the chosen path (Job Object on a working freeze vs
uv-launcher) in the program plan + memory.

---

## AC checklist

- [ ] **P0.1** frozen onedir boots; `create_schema` tables present; `/health` 200.
- [ ] **P0.2** closing the console leaves **zero** residual server/python PIDs.
- [ ] **P0.3** relaunch-against-same-DB boots clean (port free, SQLite unlocked, no orphan) — *not* "exit 0".
- [ ] **P0.4** if P0.1/P0.2 fail, uv-launcher contingency is invoked + recorded before P1–P4.
