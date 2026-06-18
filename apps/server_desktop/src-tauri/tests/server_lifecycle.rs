//! Integration test for the server lifecycle against the REAL staged
//! `yeson-server-<triple>/yeson-server` onedir binary (AC3.1 / AC3.5).
//!
//! It reuses the exact process-group spawn + SIGTERM-the-group teardown the
//! Tauri command uses (the production code lives in `server_process.rs`; this
//! test drives the same mechanism directly so it can run headless, with no GUI).
//!
//! The test self-skips (passes) when the binary for this host triple isn't
//! staged, so CI without a freeze step stays green.

#[cfg(unix)]
mod unix {
    use std::io::Read;
    use std::path::{Path, PathBuf};
    use std::process::{Child, Command, Stdio};
    use std::time::{Duration, Instant};

    fn staged_server_bin() -> Option<PathBuf> {
        let triple = if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
            "aarch64-apple-darwin"
        } else if cfg!(all(target_os = "macos", target_arch = "x86_64")) {
            "x86_64-apple-darwin"
        } else if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
            "x86_64-unknown-linux-gnu"
        } else {
            return None;
        };
        let bin = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("binaries")
            .join(format!("yeson-server-{triple}"))
            .join("yeson-server");
        bin.is_file().then_some(bin)
    }

    fn pick_free_port() -> u16 {
        std::net::TcpListener::bind(("127.0.0.1", 0))
            .unwrap()
            .local_addr()
            .unwrap()
            .port()
    }

    /// Any live PID in the process group `pgid`? Uses `pgrep -g` so we catch the
    /// forked uvicorn worker too.
    fn group_has_live_pids(pgid: i32) -> bool {
        match Command::new("pgrep").arg("-g").arg(pgid.to_string()).output() {
            Ok(o) => !o.stdout.is_empty(),
            Err(_) => false,
        }
    }

    fn forward(prefix: &'static str, pipe: Option<impl Read + Send + 'static>) {
        let Some(mut pipe) = pipe else { return };
        std::thread::spawn(move || {
            let mut buf = [0u8; 4096];
            while let Ok(n) = pipe.read(&mut buf) {
                if n == 0 {
                    break;
                }
                eprint!("[{prefix}] {}", String::from_utf8_lossy(&buf[..n]));
            }
        });
    }

    /// Spawn the staged server as a process-group leader against `db_path`,
    /// wait until it answers /health 200, SIGTERM the GROUP, and assert no
    /// surviving group member. Returns the bound port used.
    fn run_one_lifecycle(bin: &Path, db_path: &Path, storage: &Path) -> u16 {
        use std::os::unix::process::CommandExt;
        let port = pick_free_port();
        let mut child: Child = Command::new(bin)
            .env("DATABASE_URL", format!("sqlite+aiosqlite:///{}", db_path.display()))
            .env("STORAGE_ROOT", storage)
            .env("PORT", port.to_string())
            .env("HOST", "127.0.0.1")
            .env("YESON_AI_PROVIDER", "gemini_live")
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUTF8", "1")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0) // group leader: pgid == pid
            .spawn()
            .expect("spawn yeson-server");
        let pgid = child.id() as i32;
        forward("stdout", child.stdout.take());
        forward("stderr", child.stderr.take());

        // AC3.1: server comes up and answers /health 200.
        let health = format!("http://127.0.0.1:{port}/api/v1/health");
        let deadline = Instant::now() + Duration::from_secs(45);
        let mut ok = false;
        while Instant::now() < deadline {
            if let Ok(resp) = ureq::get(&health).timeout(Duration::from_secs(2)).call() {
                if resp.status() == 200 {
                    ok = true;
                    break;
                }
            }
            std::thread::sleep(Duration::from_millis(300));
        }
        assert!(ok, "server never returned /health 200 on port {port}");
        eprintln!("OK: /health 200 on port {port}, pgid={pgid}");

        // AC3.1: SIGTERM the GROUP, grace, SIGKILL backstop.
        let _ = Command::new("/bin/kill").arg("-TERM").arg(format!("-{pgid}")).status();
        let mut exited = false;
        for _ in 0..30 {
            if matches!(child.try_wait(), Ok(Some(_))) {
                exited = true;
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        if !exited {
            let _ = Command::new("/bin/kill").arg("-KILL").arg(format!("-{pgid}")).status();
        }
        let _ = child.wait();
        std::thread::sleep(Duration::from_millis(500)); // let the OS reap the worker

        assert!(
            !group_has_live_pids(pgid),
            "process group {pgid} still has live members after teardown (orphan!)"
        );
        eprintln!("OK: no surviving PID in group {pgid}");
        port
    }

    #[test]
    fn spawn_health_then_sigterm_leaves_no_orphan() {
        let Some(bin) = staged_server_bin() else {
            eprintln!("SKIP: no staged yeson-server binary for this host triple");
            return;
        };

        let tmp = std::env::temp_dir().join(format!("yeson-server-test-{}", std::process::id()));
        std::fs::create_dir_all(&tmp).unwrap();
        let db_path = tmp.join("yeson-meet.db");
        let storage = tmp.join("storage");
        std::fs::create_dir_all(&storage).unwrap();

        // First lifecycle: cold DB → boot → /health → SIGTERM group → no orphan.
        run_one_lifecycle(&bin, &db_path, &storage);

        // AC3.5: a SECOND launch against the SAME db dir starts clean — proving the
        // graceful shutdown left no stale SQLite lock and a consistent file. (This
        // is the real acceptance bar; a non-empty `-wal` between connections is
        // normal for WAL mode and is NOT corruption, so we assert "relaunch boots"
        // rather than a brittle WAL byte-size check.)
        run_one_lifecycle(&bin, &db_path, &storage);
        eprintln!("OK: relaunch against same DB started clean (no stale lock)");

        let _ = std::fs::remove_dir_all(&tmp);
    }
}
