// 대용량 mp4를 서버에서 직접 받아 사용자가 고른 경로에 저장하는 커맨드.
//
// 왜 Rust에서 받나:
//  - plugin-fs의 writeFile은 capabilities의 fs 스코프($HOME/**)에 묶여, 윈도우에서
//    다른 드라이브(D:\ 등)나 네트워크 폴더로 저장하면 거부된다.
//  - 바이트를 JS로 arrayBuffer 받아 invoke로 넘기면 수백 MB가 JSON 숫자배열이 되어
//    IPC에서 메모리/성능이 터진다(단일 Vec<u8> raw-body 최적화는 다중 인자와 못 씀).
// 그래서 URL과 목적지 경로만 넘기고, 받기+쓰기를 전부 Rust에서 처리한다.
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
