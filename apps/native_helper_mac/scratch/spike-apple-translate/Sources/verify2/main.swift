// 검증 2: SpeechTranscriber 파일 전사 + audioTimeRange (macOS 26+)
import Foundation
import AVFoundation
import Speech

func logErr(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

@available(macOS 26.0, *)
func run() async {
    let path = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "/tmp/spike-test.wav"
    let url = URL(fileURLWithPath: path)
    let start = Date()

    let locale = Locale(identifier: "en-US")

    // 자산(모델) 설치 여부 확인 + 필요시 프로그램적 다운로드 요청
    let supported = await SpeechTranscriber.supportedLocales
    logErr("supportedLocales count=\(supported.count) contains en-US? \(supported.contains { $0.identifier(.bcp47) == "en-US" })")
    let installed = await SpeechTranscriber.installedLocales
    logErr("installedLocales: \(installed.map { $0.identifier(.bcp47) })")

    let transcriber = SpeechTranscriber(
        locale: locale,
        transcriptionOptions: [],
        reportingOptions: [],
        attributeOptions: [.audioTimeRange])

    // 자산 확보: 미설치 시 AssetInventory로 다운로드 요청
    if let request = try? await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
        logErr("asset installation required — downloading... progress objects present")
        do {
            try await request.downloadAndInstall()
            logErr("asset download+install complete in \(Date().timeIntervalSince(start))s")
        } catch {
            logErr("ASSET DOWNLOAD ERROR: \(error) type=\(type(of: error))")
        }
    } else {
        logErr("no asset installation request needed (already installed or unsupported)")
    }

    let analyzer = SpeechAnalyzer(modules: [transcriber])

    let file: AVAudioFile
    do {
        file = try AVAudioFile(forReading: url)
    } catch {
        logErr("AVAudioFile open error: \(error)")
        exit(2)
    }
    logErr("audio format: \(file.processingFormat), length frames: \(file.length)")

    // 결과 수집 태스크
    let collector = Task {
        var runs = 0
        do {
            for try await result in transcriber.results {
                let text = String(result.text.characters)
                let final = result.isFinal
                logErr("RESULT isFinal=\(final) text=\"\(text)\"")
                // AttributedString run별 audioTimeRange 추출
                for run in result.text.runs {
                    if let range = run.audioTimeRange {
                        let seg = String(result.text[run.range].characters)
                        let t0 = range.start.seconds
                        let t1 = (range.start + range.duration).seconds
                        print(String(format: "  run t0=%.3f t1=%.3f  \"%@\"", t0, t1, seg))
                        runs += 1
                    }
                }
            }
        } catch {
            logErr("RESULTS ITERATION ERROR: \(error) type=\(type(of: error))")
        }
        logErr("total runs with audioTimeRange: \(runs)")
    }

    // 파일 입력을 analyzer에 물린다
    do {
        let analStart = Date()
        if let lastSample = try await analyzer.analyzeSequence(from: file) {
            logErr("analyzeSequence returned lastSampleTime=\(lastSample.seconds)")
        } else {
            logErr("analyzeSequence returned nil")
        }
        try await analyzer.finalizeAndFinishThroughEndOfInput()
        logErr("file analysis wall time: \(Date().timeIntervalSince(analStart))s for \(file.length) frames")
    } catch {
        logErr("ANALYZE ERROR: \(error) type=\(type(of: error))")
    }

    await collector.value
    logErr("total elapsed: \(Date().timeIntervalSince(start))s")
    exit(0)
}

if #available(macOS 26.0, *) {
    await run()
} else {
    logErr("requires macOS 26")
    exit(1)
}
