// === ANCHOR: VOICEMEETER_FFI_START ===
//! Voicemeeter Remote API bindings (Windows x64).
//!
//! The DLL is shipped by the user's local Voicemeeter installation;
//! we locate it at runtime via the official Uninstall registry key
//! (with legacy brand-key and default-path fallbacks) and load it with
//! the full path so `LoadLibrary` doesn't depend on search order.
//! Higher-level routing logic lives in `voicemeeter_router` (S2) and
//! consumes [`VoicemeeterClient`].

use std::ffi::{CStr, CString};
use std::os::raw::{c_char, c_float, c_long};
use std::path::PathBuf;

use libloading::{Library, Symbol};
use winreg::enums::HKEY_LOCAL_MACHINE;
use winreg::RegKey;

// Official Voicemeeter SDK locator (VoicemeeterRemoteAPI.pdf §4):
// 64-bit apps read InstallLocation/UninstallString from the uninstall key.
const UNINSTALL_KEY: &str =
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\VB:Voicemeeter {17359A74-1236-5467}";
// Legacy brand key — some setups expose VoicemeeterRemoteDir here.
const BRAND_KEY: &str = r"SOFTWARE\WOW6432Node\VB-Audio\Voicemeeter";
const BRAND_DIR_VALUE: &str = "VoicemeeterRemoteDir";
const DEFAULT_INSTALL_DIRS: &[&str] = &[
    r"C:\Program Files (x86)\VB\Voicemeeter",
    r"C:\Program Files\VB\Voicemeeter",
];
const DLL_NAME: &str = "VoicemeeterRemote64.dll";
const PARAM_STRING_BUF_LEN: usize = 512;

/// Voicemeeter edition codes returned by `VBVMR_GetVoicemeeterType`.
pub mod kind {
    use std::os::raw::c_long;
    pub const STANDARD: c_long = 1;
    pub const BANANA: c_long = 2;
    pub const POTATO: c_long = 3;
    pub const POTATO_X64: c_long = 6;
}

/// `vType` argument for `VBVMR_RunVoicemeeter`.
pub mod run_kind {
    use std::os::raw::c_long;
    pub const STANDARD: c_long = 1;
    pub const BANANA: c_long = 2;
    pub const POTATO: c_long = 3;
    pub const POTATO_X64: c_long = 6;
}

type FnLogin = unsafe extern "system" fn() -> c_long;
type FnLogout = unsafe extern "system" fn() -> c_long;
type FnRunVoicemeeter = unsafe extern "system" fn(c_long) -> c_long;
type FnGetType = unsafe extern "system" fn(*mut c_long) -> c_long;
type FnGetVersion = unsafe extern "system" fn(*mut c_long) -> c_long;
type FnIsDirty = unsafe extern "system" fn() -> c_long;
type FnGetParamFloat = unsafe extern "system" fn(*const c_char, *mut c_float) -> c_long;
type FnSetParamFloat = unsafe extern "system" fn(*const c_char, c_float) -> c_long;
type FnGetParamStringA = unsafe extern "system" fn(*const c_char, *mut c_char) -> c_long;
type FnSetParamStringA = unsafe extern "system" fn(*const c_char, *const c_char) -> c_long;
type FnSetParameters = unsafe extern "system" fn(*const c_char) -> c_long;

/// Safe wrapper around the Voicemeeter Remote API.
///
/// `_lib` keeps the DLL alive so the cached function pointers stay valid.
/// `Login` is RAII-paired with `Logout` on `Drop` (best-effort: skipped
/// on `panic = "abort"`; disk-backed recovery handles that case at
/// next startup — see S3).
pub struct VoicemeeterClient {
    _lib: Library,
    f_login: FnLogin,
    f_logout: FnLogout,
    f_run: FnRunVoicemeeter,
    f_get_type: FnGetType,
    f_get_version: FnGetVersion,
    f_is_dirty: FnIsDirty,
    f_get_float: FnGetParamFloat,
    f_set_float: FnSetParamFloat,
    f_get_string: FnGetParamStringA,
    f_set_string: FnSetParamStringA,
    f_set_script: FnSetParameters,
    logged_in: bool,
}

impl VoicemeeterClient {
    /// Locate `VoicemeeterRemote64.dll` via the registry and load it.
    pub fn load() -> Result<Self, String> {
        let dll_path = locate_dll()?;
        let lib = unsafe { Library::new(&dll_path) }
            .map_err(|error| format!("failed to load {}: {error}", dll_path.display()))?;
        unsafe { Self::from_library(lib) }
    }

    unsafe fn from_library(lib: Library) -> Result<Self, String> {
        macro_rules! sym {
            ($ty:ty, $name:expr) => {{
                let symbol: Symbol<$ty> = lib
                    .get(concat!($name, "\0").as_bytes())
                    .map_err(|error| format!("{} missing in DLL: {error}", $name))?;
                *symbol
            }};
        }

        Ok(Self {
            f_login: sym!(FnLogin, "VBVMR_Login"),
            f_logout: sym!(FnLogout, "VBVMR_Logout"),
            f_run: sym!(FnRunVoicemeeter, "VBVMR_RunVoicemeeter"),
            f_get_type: sym!(FnGetType, "VBVMR_GetVoicemeeterType"),
            f_get_version: sym!(FnGetVersion, "VBVMR_GetVoicemeeterVersion"),
            f_is_dirty: sym!(FnIsDirty, "VBVMR_IsParametersDirty"),
            f_get_float: sym!(FnGetParamFloat, "VBVMR_GetParameterFloat"),
            f_set_float: sym!(FnSetParamFloat, "VBVMR_SetParameterFloat"),
            f_get_string: sym!(FnGetParamStringA, "VBVMR_GetParameterStringA"),
            f_set_string: sym!(FnSetParamStringA, "VBVMR_SetParameterStringA"),
            f_set_script: sym!(FnSetParameters, "VBVMR_SetParameters"),
            _lib: lib,
            logged_in: false,
        })
    }

    pub fn login(&mut self) -> Result<(), String> {
        let code = unsafe { (self.f_login)() };
        // 0 = OK, 1 = already logged in by another client (still usable).
        if code == 0 || code == 1 {
            self.logged_in = true;
            Ok(())
        } else {
            Err(format!("VBVMR_Login returned {code}"))
        }
    }

    pub fn run(&self, kind: c_long) -> Result<(), String> {
        let code = unsafe { (self.f_run)(kind) };
        if code == 0 {
            Ok(())
        } else {
            Err(format!("VBVMR_RunVoicemeeter({kind}) returned {code}"))
        }
    }

    pub fn voicemeeter_type(&self) -> Result<c_long, String> {
        let mut value: c_long = 0;
        let code = unsafe { (self.f_get_type)(&mut value) };
        if code == 0 {
            Ok(value)
        } else {
            Err(format!("VBVMR_GetVoicemeeterType returned {code}"))
        }
    }

    pub fn voicemeeter_version(&self) -> Result<u32, String> {
        let mut value: c_long = 0;
        let code = unsafe { (self.f_get_version)(&mut value) };
        if code == 0 {
            Ok(value as u32)
        } else {
            Err(format!("VBVMR_GetVoicemeeterVersion returned {code}"))
        }
    }

    pub fn set_float(&self, param: &str, value: f32) -> Result<(), String> {
        let cstr = CString::new(param).map_err(|error| format!("bad param name: {error}"))?;
        let code = unsafe { (self.f_set_float)(cstr.as_ptr(), value) };
        if code == 0 {
            Ok(())
        } else {
            Err(format!("VBVMR_SetParameterFloat({param}, {value}) returned {code}"))
        }
    }

    pub fn get_float(&self, param: &str) -> Result<f32, String> {
        let cstr = CString::new(param).map_err(|error| format!("bad param name: {error}"))?;
        let mut value: c_float = 0.0;
        let code = unsafe { (self.f_get_float)(cstr.as_ptr(), &mut value) };
        if code == 0 {
            Ok(value)
        } else {
            Err(format!("VBVMR_GetParameterFloat({param}) returned {code}"))
        }
    }

    pub fn set_string(&self, param: &str, value: &str) -> Result<(), String> {
        let key = CString::new(param).map_err(|error| format!("bad param name: {error}"))?;
        let val = CString::new(value).map_err(|error| format!("bad value: {error}"))?;
        let code = unsafe { (self.f_set_string)(key.as_ptr(), val.as_ptr()) };
        if code == 0 {
            Ok(())
        } else {
            Err(format!("VBVMR_SetParameterStringA({param}) returned {code}"))
        }
    }

    pub fn get_string(&self, param: &str) -> Result<String, String> {
        let cstr = CString::new(param).map_err(|error| format!("bad param name: {error}"))?;
        let mut buf = vec![0u8; PARAM_STRING_BUF_LEN];
        let code = unsafe { (self.f_get_string)(cstr.as_ptr(), buf.as_mut_ptr() as *mut c_char) };
        if code != 0 {
            return Err(format!("VBVMR_GetParameterStringA({param}) returned {code}"));
        }
        let trimmed = CStr::from_bytes_until_nul(&buf)
            .map_err(|error| format!("string param {param} not null-terminated: {error}"))?;
        Ok(trimmed.to_string_lossy().into_owned())
    }

    pub fn apply_script(&self, script: &str) -> Result<(), String> {
        let cstr = CString::new(script).map_err(|error| format!("bad script: {error}"))?;
        let code = unsafe { (self.f_set_script)(cstr.as_ptr()) };
        if code == 0 {
            Ok(())
        } else {
            Err(format!("VBVMR_SetParameters returned {code}"))
        }
    }

    pub fn is_dirty(&self) -> bool {
        unsafe { (self.f_is_dirty)() != 0 }
    }
}

impl Drop for VoicemeeterClient {
    fn drop(&mut self) {
        if self.logged_in {
            unsafe {
                let _ = (self.f_logout)();
            }
        }
    }
}

fn locate_dll() -> Result<PathBuf, String> {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let mut tried: Vec<String> = Vec::new();

    // 1) Official SDK: uninstall key → InstallLocation
    if let Ok(key) = hklm.open_subkey(UNINSTALL_KEY) {
        if let Ok(loc) = key.get_value::<String, _>("InstallLocation") {
            let path = PathBuf::from(loc.trim()).join(DLL_NAME);
            if path.is_file() {
                return Ok(path);
            }
            tried.push(path.display().to_string());
        }
        // 2) Same key → UninstallString → parent dir of uninstaller exe
        if let Ok(raw) = key.get_value::<String, _>("UninstallString") {
            if let Some(dir) = uninstaller_dir(&raw) {
                let path = dir.join(DLL_NAME);
                if path.is_file() {
                    return Ok(path);
                }
                tried.push(path.display().to_string());
            }
        }
    }

    // 3) Legacy brand key (VoicemeeterRemoteDir value)
    if let Ok(key) = hklm.open_subkey(BRAND_KEY) {
        if let Ok(dir) = key.get_value::<String, _>(BRAND_DIR_VALUE) {
            let path = PathBuf::from(dir.trim()).join(DLL_NAME);
            if path.is_file() {
                return Ok(path);
            }
            tried.push(path.display().to_string());
        }
    }

    // 4) Conventional install paths
    for base in DEFAULT_INSTALL_DIRS {
        let path = PathBuf::from(base).join(DLL_NAME);
        if path.is_file() {
            return Ok(path);
        }
        tried.push(path.display().to_string());
    }

    if tried.is_empty() {
        Err(format!(
            "Voicemeeter not detected (no registry keys, no default install)."
        ))
    } else {
        Err(format!(
            "Voicemeeter DLL not found. Tried: [{}]",
            tried.join(", ")
        ))
    }
}

fn uninstaller_dir(uninstall_string: &str) -> Option<PathBuf> {
    let s = uninstall_string.trim();
    let exe = if let Some(rest) = s.strip_prefix('"') {
        rest.split('"').next()?.trim()
    } else {
        // No quotes — usually no args either. Strip a trailing " /uninstall" if present.
        s.split(" /").next()?.trim()
    };
    PathBuf::from(exe).parent().map(PathBuf::from)
}

/// Cheap non-loading check: is Voicemeeter installed at all?
pub fn is_installed() -> bool {
    locate_dll().is_ok()
}
// === ANCHOR: VOICEMEETER_FFI_END ===
