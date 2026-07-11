import Foundation
import Speech
import AVFoundation

/// 파일 전사: WAV/AIFF 등 오디오 파일을 SpeechAnalyzer + SpeechTranscriber로 전사하고
/// stdout JSONL로 token/progress/done 이벤트를 방출한다.
///
/// API 패턴은 스파이크 노트(docs/superpowers/specs/2026-07-11-apple-spike-notes.md
/// 검증 2)의 확정 코드를 따른다: `analyzer.analyzeSequence(from:)` +
/// `finalizeAndFinishThroughEndOfInput()`로 결과 스트림을 종료시키고, results를 순회하는
/// Task는 analyzeSequence 호출 **전**에 시작해 둔다.
@available(macOS 26.0, *)
public func runTranscribeFile(path: String) async -> Int32 {
    let url = URL(fileURLWithPath: path)
    let file: AVAudioFile
    do {
        file = try AVAudioFile(forReading: url)
    } catch {
        emit(.status(state: "error", reason: "input_open_failed: \(error)"))
        return 1
    }

    let sampleRate = file.processingFormat.sampleRate
    let durationSec = sampleRate > 0 ? Double(file.length) / sampleRate : 0

    let transcriber = SpeechTranscriber(
        locale: Locale(identifier: "en-US"),
        transcriptionOptions: [],
        reportingOptions: [],                    // 파일 전사는 파셜 불필요 — final만
        attributeOptions: [.audioTimeRange])

    // STT 모델 에셋 확보: 미설치 시 프로그램적 다운로드 시도(스파이크 검증 2, ~20s,
    // 시스템 설정 UI 없음). 실패하면 missing_stt_asset 에러로 보고하고 종료.
    do {
        if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            try await request.downloadAndInstall()
        }
    } catch {
        emit(.status(state: "error", reason: "missing_stt_asset: \(error)"))
        return 1
    }

    let analyzer = SpeechAnalyzer(modules: [transcriber])
    emit(.status(state: "ready", reason: nil))

    var lastT1 = 0.0
    var streamError: Error?
    // results 순회는 analyzeSequence 호출 전에 시작해 둔다 (스파이크 노트 주의사항).
    let collector = Task {
        do {
            for try await result in transcriber.results {
                for run in result.text.runs {
                    guard let range = run.audioTimeRange else { continue }
                    let text = String(result.text[run.range].characters)
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    if text.isEmpty { continue }
                    let t0 = range.start.seconds
                    let t1 = range.end.seconds
                    // NaN/Infinity·역행 구간 방출 방지 (Events.swift jsonLine은 try! 사용 — 트랩 회피)
                    guard t0.isFinite, t1.isFinite, t1 > t0 else { continue }
                    emit(.token(t0: t0, t1: t1, text: text))
                    if t1 > lastT1 { lastT1 = t1 }
                }
                if durationSec > 0 {
                    emit(.progress(frac: min(max(lastT1 / durationSec, 0), 1.0)))
                }
            }
        } catch {
            streamError = error
        }
    }

    do {
        _ = try await analyzer.analyzeSequence(from: file)
        try await analyzer.finalizeAndFinishThroughEndOfInput()
    } catch {
        collector.cancel()
        emit(.status(state: "error", reason: "transcribe_failed: \(error)"))
        return 1
    }
    await collector.value

    if let streamError {
        emit(.status(state: "error", reason: "transcribe_failed: \(streamError)"))
        return 1
    }

    emit(.done)
    return 0
}
