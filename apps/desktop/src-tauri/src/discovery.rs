use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use mdns_sd::{ServiceDaemon, ServiceEvent};
use serde::Serialize;

/// Returns a stable, hostname-based device label for self-enroll dedup.
/// Falls back to `client-device` if the hostname cannot be obtained.
#[tauri::command]
pub fn device_label() -> String {
    let host = hostname::get()
        .ok()
        .and_then(|h| h.into_string().ok())
        .filter(|h| !h.trim().is_empty())
        .unwrap_or_else(|| "device".to_string());
    format!("client-{host}")
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DiscoveredServer {
    pub ip: String,
    pub port: u16,
}

/// Browse the LAN for the single yeson-meet server. Returns the first resolved
/// instance within a short timeout, or None if none is found / mDNS is blocked.
#[tauri::command]
pub fn discover_server() -> Result<Option<DiscoveredServer>, String> {
    let daemon = ServiceDaemon::new().map_err(|e| format!("mDNS 시작 실패: {e}"))?;
    let receiver = match daemon.browse("_yeson-meet._tcp.local.") {
        Ok(receiver) => receiver,
        Err(error) => {
            let _ = daemon.shutdown();
            return Err(format!("mDNS 브라우즈 실패: {error}"));
        }
    };

    let deadline = Duration::from_secs(3);
    let found = loop {
        match receiver.recv_timeout(deadline) {
            Ok(ServiceEvent::ServiceResolved(info)) => {
                if let Some(addr) = info.get_addresses().iter().next() {
                    break Some(DiscoveredServer {
                        ip: addr.to_string(),
                        port: info.get_port(),
                    });
                }
            }
            Ok(_) => continue,
            Err(_) => break None, // timeout / channel closed
        }
    };
    let _ = daemon.shutdown();
    Ok(found)
}

/// Active TCP probe of an entire /24 subnet to find a yeson-meet server.
/// `base` must be three dotted octets (e.g. "192.168.0"). `port` is the
/// server port (typically 8000). Probes .1–.254 concurrently in chunks of
/// 32 OS threads; accepts a host only if HTTP GET /api/v1/health returns 200.
/// Returns sorted IPs (by last octet).
#[tauri::command]
pub fn scan_subnet(base: String, port: u16) -> Result<Vec<String>, String> {
    // Validate: must be exactly three dotted octets, each parseable as u8
    let parts: Vec<&str> = base.trim().split('.').collect();
    if parts.len() != 3 || parts.iter().any(|p| p.parse::<u8>().is_err()) {
        return Err(format!(
            "base는 세 자리 옥텟이어야 합니다 (예: 192.168.0). 입력값: {}",
            base
        ));
    }

    let found: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let hosts: Vec<u8> = (1u8..=254).collect();
    let mut handles = Vec::new();

    for chunk in hosts.chunks(32) {
        let chunk = chunk.to_vec();
        let base_clone = base.clone();
        let found_clone = Arc::clone(&found);

        let handle = thread::spawn(move || {
            for host in chunk {
                let ip = format!("{}.{}", base_clone, host);
                let addr_str = format!("{}:{}", ip, port);
                let addr = match addr_str.parse() {
                    Ok(a) => a,
                    Err(_) => continue,
                };

                if let Ok(mut stream) =
                    TcpStream::connect_timeout(&addr, Duration::from_millis(300))
                {
                    let _ = stream.set_read_timeout(Some(Duration::from_millis(400)));
                    let _ = stream.set_write_timeout(Some(Duration::from_millis(400)));

                    let req = format!(
                        "GET /api/v1/health HTTP/1.0\r\nHost: {}\r\nConnection: close\r\n\r\n",
                        ip
                    );
                    if stream.write_all(req.as_bytes()).is_ok() {
                        let mut buf = [0u8; 512];
                        let n = stream.read(&mut buf).unwrap_or(0);
                        let response = String::from_utf8_lossy(&buf[..n]);
                        if response.contains("200") {
                            if let Ok(mut lock) = found_clone.lock() {
                                lock.push(ip);
                            }
                        }
                    }
                }
            }
        });
        handles.push(handle);
    }

    for handle in handles {
        let _ = handle.join();
    }

    let mut result = match Arc::try_unwrap(found) {
        Ok(mutex) => mutex.into_inner().unwrap_or_default(),
        Err(arc) => arc.lock().map(|g| g.clone()).unwrap_or_default(),
    };

    result.sort_by_key(|ip| {
        ip.rsplit('.').next().and_then(|n| n.parse::<u8>().ok()).unwrap_or(0)
    });

    Ok(result)
}
