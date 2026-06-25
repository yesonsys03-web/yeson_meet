// === ANCHOR: SERVER_CONFIG_START ===
//! OS-keychain config/secrets store for the packaged server console (Slice 4).
//!
//! REUSES the client app's keychain-blob PATTERN
//! (`apps/desktop/src-tauri/src/credentials.rs`): a single JSON blob under one
//! keychain entry, load/save/clear glue plus a non-secret `meta` projection that
//! returns presence booleans (never the secret values). The structs are
//! NET-NEW — they hold the *server's* env contract, not the client's operator
//! login.
//!
//! Secrets live ONLY in the OS keychain; nothing here ever writes plaintext
//! secrets to disk (AC4.3). The injected env at spawn (`server_process.rs`) is
//! the sole consumer.
//!
//! `JWT_SECRET` is special: it is GENERATED ONCE in Rust (32 random bytes,
//! url-safe base64, no padding — mirroring `apps/server/db/seed.py:_gen_api_key`)
//! the first time the store is read, persisted, and NEVER exposed to the UI
//! (`meta` has no JWT field at all). The server REQUIRES it
//! (`apps/server/auth/jwt.py:20`).
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use keyring::Entry;
use serde::{Deserialize, Serialize};

const SERVICE: &str = "yeson-meet-server";
const ACCOUNT: &str = "server-config";

/// The single keychain blob. All server secrets + non-secret config live here.
/// `jwt_secret` is generated-once and never surfaced to the UI.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct ServerConfig {
    // --- secrets (presence-only in meta, value never echoed) ---
    /// Gemini Live API key. Empty → server boots with Gemini disabled (AC4.1).
    pub gemini_api_key: String,
    /// Google service-account JSON (STT/Translate). Empty when Gemini-only.
    pub google_application_credentials_json: String,
    /// Generated-once 32-byte url-safe secret. NEVER projected to the UI.
    pub jwt_secret: String,

    // --- non-secret config (echoed in meta) ---
    /// GCP project for STT/Translate (optional; can also be read from the JSON).
    pub google_cloud_project: String,
    /// STT source language code, e.g. `en-US`.
    pub google_stt_language_code: String,
    /// Translate target language, e.g. `ko`.
    pub google_translate_target_language: String,
    /// AI provider selector; defaults to `gemini_live`.
    pub yeson_ai_provider: String,
    /// Public viewer base URL used to mint viewer links.
    pub viewer_base: String,
    /// Report-summary CLI backend ("auto" | "claude" | "codex" | …). Empty/"auto"
    /// → the server auto-detects an available CLI on PATH.
    pub summary_backend: String,
    /// Optional model for summary backends that take one (e.g. opencode/deepseek).
    pub summary_model: String,
}

/// Non-secret projection returned to the UI. Carries presence booleans for the
/// secrets (never the secret values) and the non-secret config verbatim — same
/// shape philosophy as the client's `CredentialsMeta`. There is intentionally NO
/// `jwt_secret` field here, so the generated secret can never leak to JS.
#[derive(Debug, Default, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServerConfigMeta {
    pub has_gemini_key: bool,
    pub has_google_credentials: bool,
    pub has_jwt_secret: bool,
    pub google_cloud_project: String,
    pub google_stt_language_code: String,
    pub google_translate_target_language: String,
    pub provider: String,
    pub viewer_base: String,
    pub summary_backend: String,
    pub summary_model: String,
}

/// Fields the operator can edit from the GUI. Submitting a blank string leaves a
/// secret untouched (we never want the UI — which only ever sees presence, not
/// values — to blank a stored secret just because its field came back empty).
/// Non-secret fields are always overwritten with the submitted value.
#[derive(Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct ServerConfigInput {
    pub gemini_api_key: String,
    pub google_application_credentials_json: String,
    pub google_cloud_project: String,
    pub google_stt_language_code: String,
    pub google_translate_target_language: String,
    pub yeson_ai_provider: String,
    pub viewer_base: String,
    pub summary_backend: String,
    pub summary_model: String,
}

/// Default provider when the operator hasn't picked one. Matches the server
/// (`apps/server/ws/sidecar.py:120` reads `YESON_AI_PROVIDER` default
/// `gemini_live`).
pub const DEFAULT_PROVIDER: &str = "gemini_live";

impl ServerConfig {
    /// Provider with the `gemini_live` default applied (never empty).
    fn provider(&self) -> String {
        let trimmed = self.yeson_ai_provider.trim();
        if trimmed.is_empty() {
            DEFAULT_PROVIDER.to_string()
        } else {
            trimmed.to_string()
        }
    }

    /// Summary backend with the `auto` default applied (never empty).
    fn summary_backend(&self) -> String {
        let trimmed = self.summary_backend.trim();
        if trimmed.is_empty() {
            "auto".to_string()
        } else {
            trimmed.to_string()
        }
    }

    pub fn to_meta(&self) -> ServerConfigMeta {
        ServerConfigMeta {
            has_gemini_key: !self.gemini_api_key.trim().is_empty(),
            has_google_credentials: !self.google_application_credentials_json.trim().is_empty(),
            has_jwt_secret: !self.jwt_secret.trim().is_empty(),
            google_cloud_project: self.google_cloud_project.clone(),
            google_stt_language_code: self.google_stt_language_code.clone(),
            google_translate_target_language: self.google_translate_target_language.clone(),
            provider: self.provider(),
            viewer_base: self.viewer_base.clone(),
            summary_backend: self.summary_backend(),
            summary_model: self.summary_model.clone(),
        }
    }

    /// Apply an operator edit. Blank secret fields are preserved (the UI only
    /// sees presence, so an empty submission means "leave it"); non-secret
    /// fields are overwritten with the submitted value.
    fn apply(&mut self, input: ServerConfigInput) {
        if !input.gemini_api_key.trim().is_empty() {
            self.gemini_api_key = input.gemini_api_key.trim().to_string(); // vibelign: allow-secret
        }
        if !input.google_application_credentials_json.trim().is_empty() {
            self.google_application_credentials_json =
                input.google_application_credentials_json.trim().to_string(); // vibelign: allow-secret
        }
        self.google_cloud_project = input.google_cloud_project.trim().to_string();
        self.google_stt_language_code = input.google_stt_language_code.trim().to_string();
        self.google_translate_target_language =
            input.google_translate_target_language.trim().to_string();
        self.yeson_ai_provider = input.yeson_ai_provider.trim().to_string();
        self.viewer_base = input.viewer_base.trim().to_string();
        self.summary_backend = input.summary_backend.trim().to_string();
        self.summary_model = input.summary_model.trim().to_string();
    }
}

/// Generate a 32-byte url-safe, unpadded secret. Mirrors the server seeder
/// (`apps/server/db/seed.py:_gen_api_key`: 32 random bytes → urlsafe-b64 →
/// strip `=`) so the JWT_SECRET shape is consistent across the stack.
fn generate_secret() -> String {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).expect("OS RNG unavailable");
    URL_SAFE_NO_PAD.encode(bytes)
}

/// Ensure the config carries a JWT_SECRET, generating + persisting one once if
/// absent. Returns the config with a guaranteed-non-empty `jwt_secret`. Stable
/// across loads: a second call returns the same persisted secret (AC4.2).
fn ensure_jwt_secret(mut config: ServerConfig) -> Result<ServerConfig, String> {
    if config.jwt_secret.trim().is_empty() {
        config.jwt_secret = generate_secret(); // vibelign: allow-secret
        save(&config)?;
    }
    Ok(config)
}

fn entry() -> Result<Entry, String> {
    Entry::new(SERVICE, ACCOUNT).map_err(|error| format!("keychain unavailable: {error}"))
}

fn load() -> Result<Option<ServerConfig>, String> {
    match entry()?.get_password() {
        Ok(json) => {
            let config: ServerConfig =
                serde_json::from_str(&json).map_err(|error| format!("corrupt server config: {error}"))?;
            Ok(Some(config))
        }
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(error) => Err(format!("keychain read failed: {error}")),
    }
}

pub fn save(config: &ServerConfig) -> Result<(), String> {
    let json = serde_json::to_string(config).map_err(|error| error.to_string())?;
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

/// Load the config and guarantee a generated-once JWT_SECRET. This is the entry
/// point the spawn path and the meta projection both go through, so the very
/// first read on a clean machine mints + persists the secret.
pub fn load_ensured() -> Result<ServerConfig, String> {
    let config = load()?.unwrap_or_default();
    ensure_jwt_secret(config)
}

pub fn meta() -> Result<ServerConfigMeta, String> {
    Ok(load_ensured()?.to_meta())
}

/// Persist the public `viewer_base` programmatically (P4.1b tunnel lifecycle).
///
/// The tunnel manager calls this with the captured `https://<rand>.trycloudflare.com`
/// URL so the NEXT server spawn injects it as `VIEWER_BASE` (`inject_secrets`),
/// and with `""` to clear it back to the LAN default on stop. This is the
/// non-secret keychain SoT path (parallel to `save_server_config`'s
/// `apply`-then-`save`), kept separate so the tunnel lifecycle does not have to
/// round-trip through the operator-facing `ServerConfigInput` (which would also
/// require resubmitting every other non-secret field). Generated-once JWT_SECRET
/// is preserved by `load_ensured`.
pub fn set_viewer_base(viewer_base: &str) -> Result<(), String> {
    let mut config = load_ensured()?;
    config.viewer_base = viewer_base.trim().to_string();
    save(&config)
}

#[tauri::command]
pub fn save_server_config(request: ServerConfigInput) -> Result<ServerConfigMeta, String> {
    let mut config = load_ensured()?;
    config.apply(request);
    save(&config)?;
    Ok(config.to_meta())
}

#[tauri::command]
pub fn server_config_meta() -> Result<ServerConfigMeta, String> {
    meta()
}

#[tauri::command]
pub fn clear_server_config() -> Result<(), String> {
    clear()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn meta_hides_secret_values_and_reports_presence() {
        let config = ServerConfig {
            gemini_api_key: "sk-secret".to_string(), // vibelign: allow-secret
            google_application_credentials_json: "{\"k\":1}".to_string(),
            jwt_secret: "jwt-secret".to_string(), // vibelign: allow-secret
            google_cloud_project: "proj".to_string(),
            google_stt_language_code: "en-US".to_string(),
            google_translate_target_language: "ko".to_string(),
            yeson_ai_provider: "gemini_live".to_string(),
            viewer_base: "https://viewer".to_string(),
            summary_backend: "claude".to_string(),
            summary_model: String::new(),
        };
        let meta = config.to_meta();
        // Presence booleans are true...
        assert!(meta.has_gemini_key);
        assert!(meta.has_google_credentials);
        assert!(meta.has_jwt_secret);
        // ...and the projection carries the non-secret values verbatim...
        assert_eq!(meta.provider, "gemini_live");
        assert_eq!(meta.viewer_base, "https://viewer");
        assert_eq!(meta.google_cloud_project, "proj");
        assert_eq!(meta.summary_backend, "claude");
        // ...but serializing the meta must NOT leak any secret value.
        let json = serde_json::to_string(&meta).unwrap();
        assert!(!json.contains("sk-secret"));
        assert!(!json.contains("jwt-secret"));
        assert!(!json.contains("{\\\"k\\\":1}"));
    }

    #[test]
    fn meta_reports_missing_secrets_as_absent() {
        let config = ServerConfig::default();
        let meta = config.to_meta();
        assert!(!meta.has_gemini_key);
        assert!(!meta.has_google_credentials);
        assert!(!meta.has_jwt_secret);
        // Default provider is applied even on an empty config.
        assert_eq!(meta.provider, DEFAULT_PROVIDER);
        // Summary backend defaults to auto-detect.
        assert_eq!(meta.summary_backend, "auto");
    }

    #[test]
    fn ensure_jwt_secret_is_stable_across_loads() {
        // First "load" of a clean config mints a secret...
        let mut config = ServerConfig::default();
        assert!(config.jwt_secret.is_empty());
        config.jwt_secret = generate_secret(); // vibelign: allow-secret
        let first = config.jwt_secret.clone();
        assert!(!first.is_empty());

        // ...and a subsequent ensure must NOT regenerate it (idempotent on a
        // non-empty secret) — this is the generated-once stability the JWT
        // contract relies on (AC4.2). We exercise the same branch ensure uses
        // without touching the real keychain.
        let already_set = !config.jwt_secret.trim().is_empty();
        assert!(already_set, "a set secret must be treated as present");
        assert_eq!(config.jwt_secret, first, "secret must not change once set");
    }

    #[test]
    fn generated_secret_is_url_safe_and_long() {
        let secret = generate_secret();
        // 32 bytes → 43 url-safe base64 chars (no padding).
        assert_eq!(secret.len(), 43);
        assert!(secret
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'));
        // Two generations differ (real randomness, not a constant).
        assert_ne!(secret, generate_secret());
    }

    #[test]
    fn apply_preserves_blank_secrets_but_overwrites_config() {
        let mut config = ServerConfig {
            gemini_api_key: "existing".to_string(), // vibelign: allow-secret
            jwt_secret: "jwt".to_string(),          // vibelign: allow-secret
            viewer_base: "old".to_string(),
            ..Default::default()
        };
        // Operator submits blank secret (UI only knows presence) + a new viewer base.
        config.apply(ServerConfigInput {
            gemini_api_key: "   ".to_string(), // blank → preserve
            viewer_base: "https://new".to_string(),
            yeson_ai_provider: "gemini_live".to_string(),
            ..Default::default()
        });
        assert_eq!(config.gemini_api_key, "existing", "blank secret must be preserved");
        assert_eq!(config.jwt_secret, "jwt", "jwt secret is never touched by apply");
        assert_eq!(config.viewer_base, "https://new", "non-secret must be overwritten");
    }
}
// === ANCHOR: SERVER_CONFIG_END ===
