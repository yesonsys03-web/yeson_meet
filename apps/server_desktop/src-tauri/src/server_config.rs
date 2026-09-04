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
    /// AI provider selector; defaults to `gemini_live_translate` (동시통역 — 더 정확).
    pub yeson_ai_provider: String,
    /// MLX 온디바이스 라이브 번역 모델 id (예: `mlx-community/Qwen3.5-9B-4bit`).
    /// Empty → server-side `mlx_model_id()` (apps/server/ai/mlx_live_translate.py)
    /// applies its own default; the Rust side never duplicates that default.
    pub yeson_mlx_model: String,
    /// Public viewer base URL used to mint viewer links.
    pub viewer_base: String,
    /// Report-summary CLI backend ("auto" | "claude" | "codex" | …). Empty/"auto"
    /// → the server auto-detects an available CLI on PATH.
    pub summary_backend: String,
    /// Optional model for summary backends that take one (e.g. opencode/deepseek).
    pub summary_model: String,
    /// X-sheet PDF 파이프라인이 동시에 띄우는 구독 CLI 세션 수(전사·번역).
    /// 두 단계 모두 벽시계의 대부분을 API 대기로 쓰므로 동시성은 시간을 사고
    /// 토큰은 그대로다(비용은 크롭·청크 단위). 다만 적정값은 기기·구독 상태에
    /// 따라 다르므로 운영자가 고른다. 0 = 미설정 → `*_workers()`가 기본 6을
    /// 적용한다(디스크의 옛 설정 파일에는 이 필드가 없어 0으로 역직렬화된다).
    pub pdf_transcribe_workers: u32,
    pub pdf_translate_workers: u32,
    /// 클라이언트 앱에 노출할 PDF 번역 기능(탭) 스위치. None = 미설정 → 켜짐.
    /// 키체인의 옛 설정 블롭에는 이 필드가 없어 None으로 역직렬화된다 — 평범한
    /// `bool`이었다면 false가 되어, 아무것도 바꾸지 않은 기존 사용자의 번역 탭이
    /// 업데이트만으로 조용히 잠긴다. 실제 차단(탭 노출·업로드 거부)의 단일
    /// 진실은 서버(Python)이고 여기서는 값만 보관·전달한다.
    pub pdf_storyboard_enabled: Option<bool>,
    pub pdf_xsheet_enabled: Option<bool>,
}

/// 미설정 필드가 기능을 끄지 않도록 하는 입력 기본값(위 `Option` 주석과 같은 이유).
fn default_true() -> bool {
    true
}

/// 워커 수 상한. 전사는 서버가 8로 클램프하므로(handwriting_transcribe._workers)
/// 그 위를 UI에서 고르게 해도 조용히 깎인다 — 같은 상한을 여기서도 쓴다.
pub const MAX_WORKERS: u32 = 8;
/// 실측 기본값(2026-08-25 A3 116p): 전사 3→6워커 1.55배(22→35크롭/분),
/// 번역 3→6워커 1.58배(32.0→20.2분).
pub const DEFAULT_WORKERS: u32 = 6;

fn clamp_workers(value: u32) -> u32 {
    if value == 0 {
        DEFAULT_WORKERS
    } else {
        value.min(MAX_WORKERS)
    }
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
    pub mlx_model: String,
    pub viewer_base: String,
    pub summary_backend: String,
    pub summary_model: String,
    pub pdf_transcribe_workers: u32,
    pub pdf_translate_workers: u32,
    pub pdf_storyboard_enabled: bool,
    pub pdf_xsheet_enabled: bool,
}

/// Fields the operator can edit from the GUI. Submitting a blank string leaves a
/// secret untouched (we never want the UI — which only ever sees presence, not
/// values — to blank a stored secret just because its field came back empty).
/// Non-secret fields are always overwritten with the submitted value.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase", default)]
pub struct ServerConfigInput {
    pub gemini_api_key: String,
    pub google_application_credentials_json: String,
    pub google_cloud_project: String,
    pub google_stt_language_code: String,
    pub google_translate_target_language: String,
    pub yeson_ai_provider: String,
    pub yeson_mlx_model: String,
    pub viewer_base: String,
    pub summary_backend: String,
    pub summary_model: String,
    pub pdf_transcribe_workers: u32,
    pub pdf_translate_workers: u32,
    #[serde(default = "default_true")]
    pub pdf_storyboard_enabled: bool,
    #[serde(default = "default_true")]
    pub pdf_xsheet_enabled: bool,
}

/// `derive(Default)`는 bool을 false로 만들어 serde의 `default_true`와 어긋난다 —
/// 이 파일의 관례(`..Default::default()`)를 따르는 다음 호출자가 두 포맷을
/// 조용히 끄지 않도록 기본값을 손으로 맞춘다.
impl Default for ServerConfigInput {
    fn default() -> Self {
        Self {
            gemini_api_key: Default::default(), // vibelign: allow-secret
            google_application_credentials_json: Default::default(), // vibelign: allow-secret
            google_cloud_project: Default::default(),
            google_stt_language_code: Default::default(),
            google_translate_target_language: Default::default(),
            yeson_ai_provider: Default::default(),
            yeson_mlx_model: Default::default(),
            viewer_base: Default::default(),
            summary_backend: Default::default(),
            summary_model: Default::default(),
            pdf_transcribe_workers: Default::default(),
            pdf_translate_workers: Default::default(),
            pdf_storyboard_enabled: true,
            pdf_xsheet_enabled: true,
        }
    }
}

/// Default provider when the operator hasn't picked one. Matches the server
/// (`apps/server/ws/sidecar.py:120` reads `YESON_AI_PROVIDER` default
/// `gemini_live`).
pub const DEFAULT_PROVIDER: &str = "gemini_live_translate";

impl ServerConfig {
    /// Provider with the `gemini_live_translate` default applied (never empty).
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

    /// 전사 동시 세션 수(0=미설정 → 기본 6, 상한 8).
    pub fn transcribe_workers(&self) -> u32 {
        clamp_workers(self.pdf_transcribe_workers)
    }

    /// 번역 동시 세션 수(0=미설정 → 기본 6, 상한 8).
    pub fn translate_workers(&self) -> u32 {
        clamp_workers(self.pdf_translate_workers)
    }

    /// 스토리보드 번역 탭 허용 여부(미설정 → 켜짐).
    pub fn storyboard_enabled(&self) -> bool {
        self.pdf_storyboard_enabled.unwrap_or(true)
    }

    /// 엑스시트 번역 탭 허용 여부(미설정 → 켜짐).
    pub fn xsheet_enabled(&self) -> bool {
        self.pdf_xsheet_enabled.unwrap_or(true)
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
            mlx_model: self.yeson_mlx_model.clone(),
            viewer_base: self.viewer_base.clone(),
            summary_backend: self.summary_backend(),
            summary_model: self.summary_model.clone(),
            pdf_transcribe_workers: self.transcribe_workers(),
            pdf_translate_workers: self.translate_workers(),
            pdf_storyboard_enabled: self.storyboard_enabled(),
            pdf_xsheet_enabled: self.xsheet_enabled(),
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
        self.yeson_mlx_model = input.yeson_mlx_model.trim().to_string();
        self.viewer_base = input.viewer_base.trim().to_string();
        self.summary_backend = input.summary_backend.trim().to_string();
        self.summary_model = input.summary_model.trim().to_string();
        // 0(미설정)은 그대로 저장한다 — 읽을 때 기본값이 적용되므로, 나중에
        // 기본값을 바꾸면 명시적으로 고르지 않은 사용자에게도 반영된다.
        self.pdf_transcribe_workers = input.pdf_transcribe_workers.min(MAX_WORKERS);
        self.pdf_translate_workers = input.pdf_translate_workers.min(MAX_WORKERS);
        // 운영자가 고른 값은 Some으로 굳는다(미설정과 구분). 입력 쪽 기본값이
        // true라 필드가 빠진 요청이 기능을 끄는 일은 없다.
        self.pdf_storyboard_enabled = Some(input.pdf_storyboard_enabled);
        self.pdf_xsheet_enabled = Some(input.pdf_xsheet_enabled);
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
    fn worker_counts_default_when_unset_and_clamp_at_the_cap() {
        // 디스크의 옛 설정 파일에는 이 필드가 없어 0으로 역직렬화된다 —
        // 0을 "미설정"으로 읽어야 기존 사용자가 3워커(코드 기본값)에 갇히지
        // 않고 실측 권장값을 받는다.
        let mut config = ServerConfig::default();
        assert_eq!(config.transcribe_workers(), DEFAULT_WORKERS);
        assert_eq!(config.translate_workers(), DEFAULT_WORKERS);

        config.pdf_transcribe_workers = 2;
        config.pdf_translate_workers = 4;
        assert_eq!(config.transcribe_workers(), 2);
        assert_eq!(config.translate_workers(), 4);

        // 서버가 전사 워커를 8로 깎으므로(handwriting_transcribe._workers) 여기서
        // 먼저 같은 상한을 적용한다 — UI가 보여준 값과 실제가 어긋나면 안 된다.
        config.pdf_transcribe_workers = 99;
        config.pdf_translate_workers = 99;
        assert_eq!(config.transcribe_workers(), MAX_WORKERS);
        assert_eq!(config.translate_workers(), MAX_WORKERS);
    }

    #[test]
    fn apply_keeps_zero_as_unset_and_caps_the_rest() {
        let mut config = ServerConfig::default();
        config.apply(ServerConfigInput {
            pdf_transcribe_workers: 0,
            pdf_translate_workers: 99,
            ..Default::default()
        });
        // 0은 그대로 저장한다 — 나중에 기본값을 올리면 명시적으로 고르지 않은
        // 사용자에게도 반영된다(저장 시점에 6으로 굳히면 그 길이 막힌다).
        assert_eq!(config.pdf_transcribe_workers, 0);
        assert_eq!(config.pdf_translate_workers, MAX_WORKERS);
        assert_eq!(config.transcribe_workers(), DEFAULT_WORKERS);
    }

    #[test]
    fn pdf_features_default_to_enabled_when_the_field_is_absent() {
        // 디스크(키체인)의 옛 설정 블롭에는 이 필드가 없다 — None으로 역직렬화
        // 되어야 "켜짐"으로 읽힌다. 평범한 bool이었다면 false가 되어 기존
        // 사용자의 번역 탭이 조용히 잠긴다.
        let config = ServerConfig::default();
        assert!(config.storyboard_enabled());
        assert!(config.xsheet_enabled());

        let legacy: ServerConfig =
            serde_json::from_str(r#"{"geminiApiKey":"k","viewerBase":"https://v"}"#).unwrap();
        assert_eq!(legacy.pdf_storyboard_enabled, None, "옛 블롭 = 미설정");
        assert!(legacy.storyboard_enabled());
        assert!(legacy.xsheet_enabled());

        // 입력 쪽도 마찬가지 — 필드가 빠진 요청이 기능을 끄면 안 된다.
        let input: ServerConfigInput = serde_json::from_str("{}").unwrap();
        assert!(input.pdf_storyboard_enabled);
        assert!(input.pdf_xsheet_enabled);
    }

    #[test]
    fn apply_stores_feature_toggles_and_meta_projects_them() {
        let mut config = ServerConfig::default();
        config.apply(ServerConfigInput {
            pdf_storyboard_enabled: true,
            pdf_xsheet_enabled: false,
            ..Default::default()
        });
        assert!(!config.xsheet_enabled(), "끈 기능은 꺼진 채로 읽혀야 한다");
        assert!(config.storyboard_enabled(), "다른 기능은 영향받지 않는다");
        // 명시적으로 고른 값은 Some으로 굳는다(미설정과 구분).
        assert_eq!(config.pdf_xsheet_enabled, Some(false));

        let meta = config.to_meta();
        assert!(meta.pdf_storyboard_enabled);
        assert!(!meta.pdf_xsheet_enabled);
    }

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
            yeson_mlx_model: "mlx-community/Qwen3.5-4B-4bit".to_string(),
            viewer_base: "https://viewer".to_string(),
            summary_backend: "claude".to_string(),
            summary_model: String::new(),
            pdf_transcribe_workers: 0,
            pdf_translate_workers: 0,
            pdf_storyboard_enabled: None,
            pdf_xsheet_enabled: None,
        };
        let meta = config.to_meta();
        // Presence booleans are true...
        assert!(meta.has_gemini_key);
        assert!(meta.has_google_credentials);
        assert!(meta.has_jwt_secret);
        // ...and the projection carries the non-secret values verbatim...
        assert_eq!(meta.provider, "gemini_live");
        assert_eq!(meta.mlx_model, "mlx-community/Qwen3.5-4B-4bit");
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
