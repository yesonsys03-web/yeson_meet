// 대용량 mp4를 서버에서 직접 받아 사용자가 고른 경로에 저장하는 커맨드.
//
// 왜 Rust에서 받나:
//  - plugin-fs의 writeFile은 capabilities의 fs 스코프($HOME/**)에 묶여, 윈도우에서
//    다른 드라이브(D:\ 등)나 네트워크 폴더로 저장하면 거부된다.
//  - 바이트를 JS로 arrayBuffer 받아 invoke로 넘기면 수백 MB가 JSON 숫자배열이 되어
//    IPC에서 메모리/성능이 터진다(단일 Vec<u8> raw-body 최적화는 다중 인자와 못 씀).
// 그래서 URL과 목적지 경로만 넘기고, 받기+쓰기를 전부 Rust에서 처리한다.
use std::path::Path;

#[tauri::command]
pub async fn download_to_file(url: String, path: String) -> Result<(), String> {
    let resp = reqwest::get(&url).await.map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status()));
    }
    let bytes = resp.bytes().await.map_err(|e| e.to_string())?;
    std::fs::write(&path, &bytes).map_err(|e| e.to_string())?;
    Ok(())
}

/// 탐침 파일 이름 접두사 — sceneSplitLogic.ts `probeFileName`, 서버 `_PROBE_PREFIX`와
/// 같은 계약이다. 한쪽만 바꾸면 탐침이 조용히 실패해 같은 PC에서도 중계로 떨어진다.
const PROBE_PREFIX: &str = "yeson_probe_";

/// 이 접두사로 시작하는 파일만 허용한다. 두 커맨드는 사용자가 고른 익스포트 폴더를
/// 인자로 받으므로, 가드가 없으면 "아무 파일 쓰기/지우기" 표면이 열린다.
fn probe_path(path: &str) -> Result<&Path, String> {
    let p = Path::new(path);
    let ok = p
        .file_name()
        .and_then(|n| n.to_str())
        .map(|n| n.starts_with(PROBE_PREFIX))
        .unwrap_or(false);
    if !ok {
        return Err(format!("탐침 파일이 아닙니다: {path}"));
    }
    Ok(p)
}

/// 익스포트 폴더에 토큰 한 줄짜리 탐침 파일을 쓴다. 서버가 이 파일을 같은 경로에서
/// 읽어내면 두 프로세스가 같은 폴더를 보고 있다는 증거다(= 직접 굽기 가능).
///
/// plugin-fs의 writeFile은 capabilities 스코프($HOME/**)에 묶여 윈도우의 다른
/// 드라이브(D:\)·네트워크 폴더에서 거부된다 — download_to_file이 Rust로 내려간 것과
/// 같은 이유로 여기도 Rust에서 쓴다.
#[tauri::command]
pub fn probe_file_write(path: String, token: String) -> Result<(), String> {
    let p = probe_path(&path)?;
    std::fs::write(p, token.as_bytes()).map_err(|e| e.to_string())
}

/// 탐침 파일을 지운다. 없는 파일은 성공으로 본다 — 클라가 실패 경로에서도 finally로
/// 무조건 부르기 때문이다.
#[tauri::command]
pub fn probe_file_remove(path: String) -> Result<(), String> {
    let p = probe_path(&path)?;
    match std::fs::remove_file(p) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn probe_commands_refuse_non_probe_names() {
        // 이 가드가 없으면 probe_file_remove가 "아무 파일이나 지우는" 커맨드가 된다 —
        // 하필 사용자가 고른 익스포트 폴더를 인자로 받는 커맨드라 위험이 크다.
        let tmp = std::env::temp_dir().join(format!("vdt-guard-{}", std::process::id()));
        std::fs::create_dir_all(&tmp).unwrap();
        let victim = tmp.join("Scene0010.mp4");
        std::fs::write(&victim, b"user-clip").unwrap();
        let victim_s = victim.to_string_lossy().into_owned();

        assert!(probe_file_write(victim_s.clone(), "ddddccccbbbbaaaa".into()).is_err());
        assert!(probe_file_remove(victim_s).is_err());
        assert_eq!(std::fs::read(&victim).unwrap(), b"user-clip");

        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn probe_write_then_remove_roundtrip() {
        let tmp = std::env::temp_dir().join(format!("vdt-rt-{}", std::process::id()));
        std::fs::create_dir_all(&tmp).unwrap();
        let path = tmp.join("yeson_probe_aaaabbbbccccdddd.tmp");
        let path_s = path.to_string_lossy().into_owned();

        probe_file_write(path_s.clone(), "aaaabbbbccccdddd".into()).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "aaaabbbbccccdddd");

        probe_file_remove(path_s.clone()).unwrap();
        assert!(!path.exists());
        // 이미 없는 파일 삭제도 성공이어야 한다 — 클라가 finally에서 무조건 부르므로
        // 여기서 에러를 던지면 실패 경로가 요란해진다.
        probe_file_remove(path_s).unwrap();

        std::fs::remove_dir_all(&tmp).ok();
    }
}
