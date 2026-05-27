// === ANCHOR: VOICEMEETER_DUMP_START ===
//! One-shot diagnostic: connects to the local Voicemeeter Banana
//! installation, reads every Strip/Bus parameter we care about, and
//! writes the result to `vm_dump.json` next to the executable.
//!
//! Designed to be double-clicked on Windows — the console window stays
//! open until the user presses Enter, so errors and the output path
//! are visible.
//!
//! Build (from Mac, cross-compile to Windows):
//!     cargo build --release --bin voicemeeter_dump \
//!         --target x86_64-pc-windows-gnu
//! Ship `target/x86_64-pc-windows-gnu/release/voicemeeter_dump.exe`.

#[cfg(not(target_os = "windows"))]
fn main() {
    eprintln!("voicemeeter_dump is Windows-only.");
    std::process::exit(1);
}

#[cfg(target_os = "windows")]
fn main() {
    let result = run();
    match &result {
        Ok(path) => {
            println!("\n[vm_dump] OK — wrote {}", path.display());
        }
        Err(error) => {
            eprintln!("\n[vm_dump] ERROR: {error}");
        }
    }
    println!("\nPress Enter to close...");
    let mut buf = String::new();
    let _ = std::io::stdin().read_line(&mut buf);
    if result.is_err() {
        std::process::exit(2);
    }
}

#[cfg(target_os = "windows")]
fn run() -> Result<std::path::PathBuf, String> {
    use serde_json::{json, Map, Value};
    use yeson_meet_lib::audio::voicemeeter_ffi::{kind, VoicemeeterClient};

    let mut client = VoicemeeterClient::load()
        .map_err(|error| format!("DLL load failed: {error}"))?;
    client
        .login()
        .map_err(|error| format!("VBVMR_Login failed: {error}"))?;

    std::thread::sleep(std::time::Duration::from_millis(400));

    let vm_type = client.voicemeeter_type().unwrap_or(0);
    let vm_version_raw = client.voicemeeter_version().unwrap_or(0);
    let edition = match vm_type {
        kind::STANDARD => "Standard",
        kind::BANANA => "Banana",
        kind::POTATO => "Potato",
        kind::POTATO_X64 => "Potato (x64)",
        _ => "Unknown",
    };
    let version_string = format!(
        "{}.{}.{}.{}",
        (vm_version_raw >> 24) & 0xff,
        (vm_version_raw >> 16) & 0xff,
        (vm_version_raw >> 8) & 0xff,
        vm_version_raw & 0xff
    );
    let lane_count = match vm_type {
        kind::BANANA => 5,
        kind::POTATO | kind::POTATO_X64 => 8,
        _ => 3,
    };

    let string_params = [
        "label",
        "device.name",
        "device.sr",
        "device.wdm",
        "device.mme",
        "device.ks",
        "device.asio",
    ];
    let strip_floats = ["A1", "A2", "A3", "A4", "A5", "B1", "B2", "B3", "mute", "gain"];
    let bus_floats = ["mute", "gain", "mono", "sel"];

    let mut strips: Vec<Value> = Vec::with_capacity(lane_count);
    for index in 0..lane_count {
        let mut obj = Map::new();
        obj.insert("index".into(), Value::from(index));
        for key in string_params {
            obj.insert(
                key.into(),
                Value::from(
                    client
                        .get_string(&format!("Strip[{index}].{key}"))
                        .unwrap_or_default(),
                ),
            );
        }
        for key in strip_floats {
            if let Ok(value) = client.get_float(&format!("Strip[{index}].{key}")) {
                obj.insert(key.into(), Value::from(value));
            }
        }
        strips.push(Value::Object(obj));
    }

    let mut buses: Vec<Value> = Vec::with_capacity(lane_count);
    for index in 0..lane_count {
        let mut obj = Map::new();
        obj.insert("index".into(), Value::from(index));
        for key in string_params {
            obj.insert(
                key.into(),
                Value::from(
                    client
                        .get_string(&format!("Bus[{index}].{key}"))
                        .unwrap_or_default(),
                ),
            );
        }
        for key in bus_floats {
            if let Ok(value) = client.get_float(&format!("Bus[{index}].{key}")) {
                obj.insert(key.into(), Value::from(value));
            }
        }
        buses.push(Value::Object(obj));
    }

    let report = json!({
        "edition": edition,
        "edition_code": vm_type,
        "version": version_string,
        "version_raw": vm_version_raw,
        "lane_count": lane_count,
        "strips": strips,
        "buses": buses,
    });

    let text = serde_json::to_string_pretty(&report)
        .map_err(|error| format!("serialize failed: {error}"))?;

    let exe_path = std::env::current_exe()
        .map_err(|error| format!("current_exe failed: {error}"))?;
    let output_path = exe_path
        .parent()
        .map(|dir| dir.join("vm_dump.json"))
        .unwrap_or_else(|| std::path::PathBuf::from("vm_dump.json"));
    std::fs::write(&output_path, &text).map_err(|error| {
        format!("write to {} failed: {error}", output_path.display())
    })?;

    println!("[vm_dump] Edition : {edition}");
    println!("[vm_dump] Version : {version_string}");
    println!("[vm_dump] Lanes   : {lane_count} strips × {lane_count} buses");

    Ok(output_path)
}
// === ANCHOR: VOICEMEETER_DUMP_END ===
