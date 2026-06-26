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
