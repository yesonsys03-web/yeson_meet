// === ANCHOR: CREDENTIALS_START ===
//! OS-keychain credential store for one-click meeting start.
//!
//! A single JSON blob (server address + operator email/password + Device API
//! Key) is stored under one keychain entry. The pure projection/selection
//! helpers (`Credentials::to_meta`, `choose_device_key`) hold all the logic and
//! are unit-tested; the `keyring` I/O is thin glue (integration-tested by hand).
use keyring::Entry;
use serde::{Deserialize, Serialize};

const SERVICE: &str = "yeson-meet";
const ACCOUNT: &str = "operator-credentials";

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Credentials {
    pub server_ws_base: String,
    pub email: String,
    pub password: String,
    pub device_api_key: String,
}

/// Non-secret projection returned to the UI. Never carries the password or the
/// Device API Key — only whether a key is present.
#[derive(Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CredentialsMeta {
    pub has_credentials: bool,
    pub server_ws_base: String,
    pub email: String,
    pub has_device_key: bool,
}

/// Transient login bundle handed to JS only to perform the operator login.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OperatorLogin {
    pub server_ws_base: String,
    pub email: String,
    pub password: String,
}

impl Credentials {
    pub fn to_meta(&self) -> CredentialsMeta {
        CredentialsMeta {
            has_credentials: true,
            server_ws_base: self.server_ws_base.clone(),
            email: self.email.clone(),
            has_device_key: !self.device_api_key.trim().is_empty(),
        }
    }
}

pub fn empty_meta() -> CredentialsMeta {
    CredentialsMeta {
        has_credentials: false,
        server_ws_base: String::new(),
        email: String::new(),
        has_device_key: false,
    }
}

/// Pick the Device API Key for a sidecar start: prefer an explicit request key,
/// otherwise fall back to the stored key. Pure so it can be unit-tested without
/// touching the keychain.
pub fn choose_device_key(request_key: &str, stored: Option<&str>) -> Result<String, String> {
    let trimmed = request_key.trim();
    if !trimmed.is_empty() {
        if trimmed.contains('<') {
            return Err("YESON_DEVICE_API_KEY is required before starting the sidecar".to_string());
        }
        return Ok(trimmed.to_string());
    }
    match stored.map(str::trim).filter(|key| !key.is_empty()) {
        Some(key) => Ok(key.to_string()),
        None => Err("no Device API Key found — register credentials or paste a key".to_string()),
    }
}

fn entry() -> Result<Entry, String> {
    Entry::new(SERVICE, ACCOUNT).map_err(|error| format!("keychain unavailable: {error}"))
}

fn load() -> Result<Option<Credentials>, String> {
    match entry()?.get_password() {
        Ok(json) => {
            let creds: Credentials =
                serde_json::from_str(&json).map_err(|error| format!("corrupt credentials: {error}"))?;
            Ok(Some(creds))
        }
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(format!("keychain read failed: {error}")),
    }
}

pub fn save(creds: &Credentials) -> Result<(), String> {
    let json = serde_json::to_string(creds).map_err(|error| error.to_string())?;
    entry()?
        .set_password(&json)
        .map_err(|error| format!("keychain write failed: {error}"))
}

pub fn clear() -> Result<(), String> {
    match entry()?.delete_credential() {
        Ok(()) => Ok(()),
        Err(keyring::Error::NoEntry) => Ok(()),
        Err(error) => Err(format!("keychain delete failed: {error}")),
    }
}

pub fn meta() -> Result<CredentialsMeta, String> {
    Ok(load()?.map(|creds| creds.to_meta()).unwrap_or_else(empty_meta))
}

pub fn operator_login() -> Result<OperatorLogin, String> {
    let creds = load()?.ok_or_else(|| "no stored credentials".to_string())?;
    Ok(OperatorLogin {
        server_ws_base: creds.server_ws_base,
        email: creds.email,
        password: creds.password,
    })
}

/// Resolve the Device API Key for `start_sidecar`: explicit request key wins,
/// else the stored key. Reads the keychain only when the request key is empty.
pub fn resolve_device_key(request_key: &str) -> Result<String, String> {
    if !request_key.trim().is_empty() {
        return choose_device_key(request_key, None);
    }
    let stored = load()?.map(|creds| creds.device_api_key);
    choose_device_key(request_key, stored.as_deref())
}

#[tauri::command]
pub fn save_credentials(request: Credentials) -> Result<(), String> {
    save(&request)
}

#[tauri::command]
pub fn clear_credentials() -> Result<(), String> {
    clear()
}

#[tauri::command]
pub fn credentials_meta() -> Result<CredentialsMeta, String> {
    meta()
}

#[tauri::command]
pub fn load_operator_login() -> Result<OperatorLogin, String> {
    operator_login()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn to_meta_hides_password_and_device_key() {
        let creds = Credentials {
            server_ws_base: "wss://host".to_string(),
            email: "op@yeson.local".to_string(),
            password: "s3cret".to_string(),
            device_api_key: "dev-key".to_string(),
        };
        let meta = creds.to_meta();
        assert_eq!(meta.has_credentials, true);
        assert_eq!(meta.server_ws_base, "wss://host");
        assert_eq!(meta.email, "op@yeson.local");
        assert_eq!(meta.has_device_key, true);
    }

    #[test]
    fn to_meta_reports_missing_device_key() {
        let creds = Credentials {
            server_ws_base: "wss://host".to_string(),
            email: "op@yeson.local".to_string(),
            password: "s3cret".to_string(),
            device_api_key: "   ".to_string(),
        };
        assert_eq!(creds.to_meta().has_device_key, false);
    }

    #[test]
    fn choose_device_key_prefers_request_key() {
        assert_eq!(choose_device_key("req-key", Some("stored")), Ok("req-key".to_string()));
    }

    #[test]
    fn choose_device_key_falls_back_to_stored() {
        assert_eq!(choose_device_key("  ", Some("stored")), Ok("stored".to_string()));
    }

    #[test]
    fn choose_device_key_rejects_placeholder() {
        assert!(choose_device_key("<plaintext-device-key>", Some("stored")).is_err());
    }

    #[test]
    fn choose_device_key_errors_when_nothing_available() {
        assert!(choose_device_key("", None).is_err());
    }
}
// === ANCHOR: CREDENTIALS_END ===
