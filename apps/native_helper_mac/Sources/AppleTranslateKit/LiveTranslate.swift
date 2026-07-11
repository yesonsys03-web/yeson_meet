import Foundation
import Speech
import AVFoundation
import Translation

/// 실시간(live) 스트리밍 전사 + 스로틀 번역.
///
/// API 패턴은 스파이크 노트(docs/superpowers/specs/2026-07-11-apple-spike-notes.md
/// 검증 3)의 확정 코드를 따른다: `AsyncStream<AnalyzerInput>` + `analyzer.start(inputSequence:)`
/// + `reportingOptions: [.volatileResults]` + `result.isFinal`로 파셜/파이널 구분.
/// 입력 PCM은 `SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith:)`(스파이크 실측:
/// 1ch/16kHz/Int16)로 변환해 넣는다. stdin이 16kHz mono s16le이므로 채널/샘플레이트는
/// 이미 일치 — 포맷 불일치 시에만 AVAudioConverter가 필요하지만, 계약상 입력이 항상
/// 16kHz mono s16le이므로 버퍼를 그 포맷으로 직접 구성한다(FileTranscribe와 동일하게
/// 변환 없이 직결; bestAvailableAudioFormat 자체가 16kHz Int16임을 스파이크가 확인함).
@available(macOS 26.0, *)
@MainActor
public func runLive() async -> Int32 {
    let transcriber = SpeechTranscriber(
        locale: Locale(identifier: "en-US"),
        transcriptionOptions: [],
        reportingOptions: [.volatileResults],
        attributeOptions: [.audioTimeRange])

    // STT 모델 에셋 확보: 미설치 시 프로그램적 다운로드 시도(FileTranscribe와 동일 패턴).
    do {
        if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            try await request.downloadAndInstall()
        }
    } catch {
        emit(.status(state: "error", reason: "missing_stt_asset: \(error)"))
        return 1
    }

    guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
        emit(.status(state: "error", reason: "no_compatible_audio_format"))
        return 1
    }
    let analyzer = SpeechAnalyzer(modules: [transcriber])

    // 라이브 경로 세션: 파셜/파이널 전략을 각각 env로 고른다(기본 둘 다 high — 사용자
    // 결정: 라이브 자막 품질 평가 우선, 화면 지속 문장 품질 > 파셜 스냅성). 두 전략이
    // 같으면 세션 하나만 만든다(동일 세션 2개 스폰 방지). 미설치 폴백/26.4 미만 단일
    // 세션은 SessionFactory가 담당. 에셋이 하나도 없으면 AppleMTMissingAsset를 던지므로
    // ready 이전에 잡아 missing_mt_asset로 표면화한다(Python 접두사 분류기 → 영구 에러).
    let env = ProcessInfo.processInfo.environment
    let partialStrategy = AppleMTStrategy.from(
        env["YESON_APPLE_LIVE_PARTIAL_STRATEGY"], defaultTo: .high)
    let finalStrategy = AppleMTStrategy.from(
        env["YESON_APPLE_LIVE_FINAL_STRATEGY"], defaultTo: .high)
    let source = Locale.Language(identifier: "en")
    let target = Locale.Language(identifier: "ko")
    let partialSession: TranslationSession
    let finalSession: TranslationSession
    do {
        if partialStrategy == finalStrategy {
            let single = try await makeTranslationSession(
                source: source, target: target, strategy: finalStrategy)
            partialSession = single
            finalSession = single
        } else if #available(macOS 26.4, *) {
            // 전략이 다르고 전략 API가 있는 26.4+에서만 두 세션을 만든다.
            finalSession = try await makeTranslationSession(
                source: source, target: target, strategy: finalStrategy)
            partialSession = try await makeTranslationSession(
                source: source, target: target, strategy: partialStrategy)
        } else {
            // 26.4 미만: 전략 구분 불가 → 오늘과 동일하게 단일 세션(파이널 전략 기준).
            let single = try await makeTranslationSession(
                source: source, target: target, strategy: finalStrategy)
            partialSession = single
            finalSession = single
        }
    } catch {
        emit(.status(state: "error", reason: "\(error)"))
        return 1
    }

    emit(.status(state: "ready", reason: nil))

    // stdin 리더: 16kHz mono s16le PCM → AVAudioPCMBuffer → analyzer 입력 스트림.
    // runLive는 @MainActor이므로 평범한 Task{}는 격리를 상속해 메인 액터에서 블로킹
    // readData를 돌게 된다 — 반드시 .detached로 메인 액터 밖에서 실행한다.
    let (inputStream, inputContinuation) = AsyncStream.makeStream(of: AnalyzerInput.self)
    let inputTask = Task.detached {
        let stdin = FileHandle.standardInput
        while true {
            // 매 반복 취소 여부 확인: readData 자체는 청크 하나만큼 블로킹될 수 있지만,
            // 취소 신호가 readData 호출 사이에서라도 관측되도록 루프 최상단에서 체크한다
            // (에러 경로에서 inputTask.cancel()이 실제로 루프를 끊게 하기 위함).
            if Task.isCancelled { break }
            let data = stdin.readData(ofLength: 3200)   // 100ms @ 16kHz s16le mono
            if data.isEmpty { break }                    // EOF — 오디오 종료
            let frames = AVAudioFrameCount(data.count / 2)
            guard frames > 0,
                  let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)
            else { continue }
            buf.frameLength = frames
            data.withUnsafeBytes { raw in
                buf.int16ChannelData!.pointee.update(
                    from: raw.bindMemory(to: Int16.self).baseAddress!,
                    count: Int(frames))
            }
            inputContinuation.yield(AnalyzerInput(buffer: buf))
        }
        inputContinuation.finish()
        // 입력 종료(또는 취소)를 analyzer에도 알려야 results AsyncSequence가 끝난다 (스파이크
        // 노트 검증 3: cont.finish() 만으로는 부족 — finalizeAndFinishThroughEndOfInput() 필수).
        // 스트리밍 입력은 이 detached task에서 계속 도착하므로, 루프를 벗어난 이 지점에서
        // 바로 호출해야 결과 수집 Task의 `for try await result in transcriber.results`가
        // 종료된다. 실패해도 치명적이지 않지만(에러 무시 시 진단 불가) stderr에 로그는 남긴다.
        do {
            try await analyzer.finalizeAndFinishThroughEndOfInput()
        } catch {
            FileHandle.standardError.write(
                "live: finalizeAndFinishThroughEndOfInput failed: \(error)\n".data(using: .utf8)!)
        }
    }

    var policy = LiveEmitPolicy(throttleMs: 500, minDeltaChars: 4)
    var utteranceStart = 0.0
    var lastGoodT1 = 0.0
    var streamError: Error?

    // 번역 실패 추적: 단발 실패는 EN-as-ko로 폴백하되(회의 중 일시적 blip이 세션을
    // 죽이면 안 됨), 5회 **연속** 실패하면 언어팩(EN→KO) 미설치로 판단해
    // missing_mt_asset를 emit하고 세션을 nonzero로 종료한다 — 이는 Python
    // 접두사 분류기(missing_mt_asset)에 영구 에러로 도달한다. 성공 시 카운터 리셋.
    let maxTranslateFailures = 5
    var translateFailures = 0
    var mtAssetError: String?
    func translateTracked(_ text: String, session: TranslationSession) async -> String {
        do {
            let ko = try await session.translations(
                from: [.init(sourceText: text)]).first?.targetText ?? text
            translateFailures = 0
            return ko
        } catch {
            translateFailures += 1
            FileHandle.standardError.write(
                ("live: translate failed (\(translateFailures)/\(maxTranslateFailures)), "
                 + "falling back to en: \(error)\n").data(using: .utf8)!)
            if translateFailures >= maxTranslateFailures {
                mtAssetError = "\(error)"
            }
            return text  // EN-as-ko 폴백 (첫 실패들은 세션을 죽이지 않음)
        }
    }

    // 결과 순회 Task를 analyzer.start(inputSequence:) 호출 **전**에 시작해 둔다 — 스파이크
    // 노트(검증 3) 경고 및 FileTranscribe의 collector 패턴과 동일. 번역 호출(translateTracked)은
    // @MainActor 격리를 유지해야 하므로 명시적으로 @MainActor 클로저로 감싼다.
    // 문장 단위 분절기: 볼래틸 스냅샷을 문장 경계/백스톱/idle로 잘라 강제 파이널을
    // 만든다(SentenceSegmenter — Gemini TranscriptAssembler 의미론 이식). 파셜은 분절기가
    // 남긴 미소비 suffix만 보여줘 이미 확정한 문장이 파셜 라인에 재노출되지 않게 한다.
    let collector = Task { @MainActor in
        var segmenter = SentenceSegmenter()

        // 강제 컷/진짜 파이널 공통 방출: finalSession으로 번역 후 .final emit + seq 증가.
        // 반환 false = 5회 연속 번역 실패(mtAssetError) → 순회 중단 신호.
        // t0/t1: 기존 finite-guard 유지하되 t0는 항상 utteranceStart(단조 보장), t1은
        // 가용한 audioTimeRange 끝(강제 컷은 근사 — 볼래틸 결과의 현재 오디오 위치)로.
        func emitFinal(text: String, result: SpeechTranscriber.Result, now: Double) async -> Bool {
            let ko = await translateTracked(text, session: finalSession)
            if mtAssetError != nil { return false }
            let t0 = utteranceStart
            var t1 = lastGoodT1
            if let range = finalizedAudioTimeRange(of: result) {
                let candidateT1 = range.end.seconds
                if candidateT1.isFinite, candidateT1 > t0 { t1 = candidateT1 }
            }
            if !(t1 > t0) { t1 = t0 }  // 최후 방어: 역행/동일값이면 0폭
            emit(.final(seq: policy.seq, en: text, ko: ko, t0: t0, t1: t1))
            policy.onFinal(now: now)
            utteranceStart = t1
            lastGoodT1 = t1
            return true
        }

        do {
            for try await result in transcriber.results {
                let en = String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
                if en.isEmpty { continue }
                let now = ProcessInfo.processInfo.systemUptime

                if result.isFinal {
                    // 진짜 isFinal: 분절기 잔여 suffix를 파이널로 flush(발화 종료 → 내부 reset).
                    var stop = false
                    for sgmt in segmenter.onFinal(en: en, now: now) {
                        if !(await emitFinal(text: sgmt.text, result: result, now: now)) {
                            stop = true; break
                        }
                    }
                    if stop { break }
                } else {
                    // 볼래틸 스냅샷: 문장 단위 강제 컷들을 먼저 방출.
                    var stop = false
                    for sgmt in segmenter.onSnapshot(en: en, now: now) {
                        if !(await emitFinal(text: sgmt.text, result: result, now: now)) {
                            stop = true; break
                        }
                    }
                    if stop { break }
                    // 파셜: 미소비 suffix만 스로틀 후 partialSession으로 번역·방출.
                    let suffix = segmenter.currentSuffix
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    if !suffix.isEmpty, let snapshot = policy.onVolatile(en: suffix, now: now) {
                        let ko = await translateTracked(snapshot, session: partialSession)
                        if mtAssetError != nil { break }  // 5회 연속 번역 실패 → 세션 종료
                        emit(.partial(seq: policy.seq, en: snapshot, ko: ko))
                    }
                }
            }
        } catch {
            streamError = error
        }
    }

    do {
        try await analyzer.start(inputSequence: inputStream)
    } catch {
        // analyzer.start 자체가 실패한 에러 경로: inputTask.value를 먼저 기다리면 부모가
        // stdin을 닫을 때까지 블로킹될 수 있으므로(파인딩 1) 절대 여기서 await하지 않는다.
        // cancel()은 best-effort로만 남기고, 실제 프로세스 종료는 main.swift의 exit()가
        // 담당한다 — exit()는 남아있는 detached task를 포함해 전부 reap하므로 안전하다.
        inputTask.cancel()
        collector.cancel()
        emit(.status(state: "error", reason: "live_failed: \(error)"))
        return 1
    }

    await collector.value

    if let mtAssetError {
        // 5회 연속 번역 실패 = 언어팩(EN→KO) 미설치로 판단 — Python 접두사 분류기에
        // missing_mt_asset(영구 에러)로 도달한다. inputTask는 이 프로세스 exit()와 함께 정리.
        inputTask.cancel()
        emit(.status(state: "error", reason: "missing_mt_asset: \(mtAssetError)"))
        return 1
    }

    if let streamError {
        // 결과 순회 도중 발생한 에러도 동일하게: inputTask.value를 기다리지 않고 즉시 보고한다.
        // 스트림 에러 시점에 stdin이 아직 열려 있어도(부모가 더 보낼 데이터가 있어도)
        // inputTask는 이 프로세스 종료(exit())와 함께 정리된다.
        inputTask.cancel()
        emit(.status(state: "error", reason: "live_failed: \(streamError)"))
        return 1
    }

    // 정상 종료 경로: collector가 에러 없이 끝났다는 것은 transcriber.results가 정상
    // 종료됐다는 뜻이고, 이는 inputTask가 이미 EOF를 확인하고 finalizeAndFinishThroughEndOfInput()
    // 호출까지 마쳤다는 뜻이므로(그 호출이 results 종료를 유발) 아래 await는 즉시 반환된다.
    inputTask.cancel()
    await inputTask.value

    return 0
}

/// 파이널 결과의 오디오 시간 범위를 run 속성에서 추출 (FileTranscribe와 동일 방식).
/// 여러 run에 걸쳐 있으면 첫 run의 시작 ~ 마지막 run의 끝을 사용.
@available(macOS 26.0, *)
private func finalizedAudioTimeRange(of result: SpeechTranscriber.Result) -> CMTimeRange? {
    var start: CMTime?
    var end: CMTime?
    for run in result.text.runs {
        guard let range = run.audioTimeRange else { continue }
        if start == nil { start = range.start }
        end = range.end
    }
    guard let s = start, let e = end else { return nil }
    return CMTimeRange(start: s, end: e)
}

