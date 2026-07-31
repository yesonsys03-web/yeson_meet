// PDF 스토리보드 번역 업로드 커맨드 — video_upload::upload_video_file과 동형.
// 173MB급 스토리보드도 스트리밍 전송으로 메모리를 상수로 유지한다.

use std::path::Path;

#[tauri::command]
pub async fn upload_pdf_file(
    upload_url: String,
    path: String,
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
        .unwrap_or("upload.pdf")
        .to_string();

    let stream = tokio_util::io::ReaderStream::new(file);
    let part = reqwest::multipart::Part::stream_with_length(
        reqwest::Body::wrap_stream(stream),
        len,
    )
    .file_name(file_name)
    .mime_str("application/pdf")
    .map_err(|e| e.to_string())?;

    let mut form = reqwest::multipart::Form::new()
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
