// 폴더 일괄 업로드용 커맨드 (자막 메이커).
//
// 왜 Rust에서 하나:
//  - 폴더 선택을 <input webkitdirectory>로 하면 WebView2 런타임 버전에 따라
//    폴더 피커가 열리지 않는 회귀가 있다(2026-07-08 Windows 실기기 재현).
//    네이티브 다이얼로그(plugin-dialog)로 폴더를 고르면 웹뷰는 그 안의 파일
//    내용을 읽을 수 없으므로, 파일 열거 + 멀티파트 업로드까지 Rust가 맡는다.
//  - GB급 원본도 스트리밍 전송으로 메모리를 상수로 유지한다(download_to_file의
//    역방향; 같은 이유로 rustls-tls).

use std::path::Path;

// videoBatch.ts VIDEO_EXTS와 동일 목록 — 한쪽만 바꾸면 폴더/파일 선택 결과가 어긋난다.
const VIDEO_EXTS: [&str; 12] = [
    "mp4", "mov", "mkv", "avi", "webm", "m4v", "mpg", "mpeg", "wmv", "flv", "ts", "3gp",
];

const MAX_DEPTH: usize = 8; // 심링크 루프/비정상 트리 방어

#[derive(serde::Serialize)]
pub struct VideoFileEntry {
    pub path: String,
    pub name: String,
}

fn is_video(name: &str) -> bool {
    Path::new(name)
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| VIDEO_EXTS.contains(&e.to_ascii_lowercase().as_str()))
        .unwrap_or(false)
}

fn walk(dir: &Path, depth: usize, out: &mut Vec<VideoFileEntry>) {
    if depth > MAX_DEPTH {
        return;
    }
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            walk(&path, depth + 1, out);
        } else if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
            if is_video(name) {
                out.push(VideoFileEntry {
                    path: path.to_string_lossy().into_owned(),
                    name: name.to_string(),
                });
            }
        }
    }
}

/// 폴더(하위 폴더 포함)에서 영상 파일만 골라 돌려준다 — webkitdirectory와 동일 범위.
#[tauri::command]
pub fn list_video_files(dir: String) -> Result<Vec<VideoFileEntry>, String> {
    let root = Path::new(&dir);
    if !root.is_dir() {
        return Err(format!("폴더가 아닙니다: {dir}"));
    }
    let mut out = Vec::new();
    walk(root, 0, &mut out);
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn is_video_matches_extensions_case_insensitively() {
        assert!(is_video("a.mp4"));
        assert!(is_video("B.MOV"));
        assert!(is_video("c.MkV"));
        assert!(!is_video("d.srt"));
        assert!(!is_video("noext"));
    }

    #[test]
    fn list_video_files_walks_subdirs_and_filters() {
        let tmp = std::env::temp_dir().join(format!("vut-{}", std::process::id()));
        let sub = tmp.join("sub");
        std::fs::create_dir_all(&sub).unwrap();
        std::fs::write(tmp.join("a.mp4"), b"x").unwrap();
        std::fs::write(tmp.join("skip.txt"), b"x").unwrap();
        std::fs::write(sub.join("b.mov"), b"x").unwrap();
        let out = list_video_files(tmp.to_string_lossy().into_owned()).unwrap();
        let names: Vec<&str> = out.iter().map(|e| e.name.as_str()).collect();
        assert_eq!(names, vec!["a.mp4", "b.mov"]);
        std::fs::remove_dir_all(&tmp).ok();
    }
}

/// 영상 한 개를 서버 업로드 엔드포인트로 멀티파트 스트리밍 전송한다.
/// 성공 시 서버 응답 본문(job_id JSON)을 그대로 돌려준다.
#[tauri::command]
pub async fn upload_video_file(
    upload_url: String,
    path: String,
    whisper_model: String,
    title: String,
    translate_provider: Option<String>,
    translate_cli_model: Option<String>,
) -> Result<String, String> {
    let file = tokio::fs::File::open(&path)
        .await
        .map_err(|e| format!("파일 열기 실패: {e}"))?;
    let len = file
        .metadata()
        .await
        .map_err(|e| format!("파일 정보 실패: {e}"))?
        .len();
    let file_name = Path::new(&path)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("upload.mp4")
        .to_string();

    let stream = tokio_util::io::ReaderStream::new(file);
    let part = reqwest::multipart::Part::stream_with_length(
        reqwest::Body::wrap_stream(stream),
        len,
    )
    .file_name(file_name)
    .mime_str("application/octet-stream")
    .map_err(|e| e.to_string())?;

    let mut form = reqwest::multipart::Form::new()
        .text("whisper_model", whisper_model)
        .text("title", title)
        .part("file", part);
    if let Some(p) = translate_provider.filter(|s| !s.is_empty()) {
        form = form.text("translate_provider", p);
    }
    if let Some(m) = translate_cli_model.filter(|s| !s.is_empty()) {
        form = form.text("translate_cli_model", m);
    }

    let resp = reqwest::Client::new()
        .post(&upload_url)
        .multipart(form)
        .send()
        .await
        .map_err(|e| format!("업로드 실패: {e}"))?;
    let status = resp.status();
    let body = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        let tail: String = body.chars().take(300).collect();
        return Err(format!("HTTP {status}: {tail}"));
    }
    Ok(body)
}
