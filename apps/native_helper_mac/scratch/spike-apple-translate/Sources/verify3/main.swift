// 검증 3: SpeechTranscriber 스트리밍 — volatileResults 옵션으로 파셜 수신
import Foundation
import AVFoundation
import Speech

func logErr(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

@available(macOS 26.0, *)
func run() async {
    let path = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "/tmp/spike-test.wav"
    let url = URL(fileURLWithPath: path)

    let transcriber = SpeechTranscriber(
        locale: Locale(identifier: "en-US"),
        transcriptionOptions: [],
        reportingOptions: [.volatileResults],   // 파셜(volatile) 결과 활성화
        attributeOptions: [.audioTimeRange])

    // 자산 확보
    if let request = try? await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
        logErr("downloading asset...")
        try? await request.downloadAndInstall()
    }

    // analyzer가 요구하는 최적 입력 포맷
    guard let analyzerFormat = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else {
        logErr("no compatible audio format"); exit(2)
    }
    logErr("analyzer format: \(analyzerFormat)")

    let analyzer = SpeechAnalyzer(modules: [transcriber])

    // 입력 스트림 (실시간 PCM 버퍼 공급)
    let (stream, continuation) = AsyncStream<AnalyzerInput>.makeStream()

    // 결과 수집: 파셜(volatile)/파이널 순서 관찰
    var volatileCount = 0
    var finalCount = 0
    let collector = Task {
        do {
            for try await result in transcriber.results {
                let text = String(result.text.characters)
                if result.isFinal {
                    finalCount += 1
                    print(String(format: "[FINAL   #%d] range=%.2f-%.2f  \"%@\"",
                                 finalCount, result.range.start.seconds, result.range.end.seconds, text))
                } else {
                    volatileCount += 1
                    print(String(format: "[VOLATILE #%d] \"%@\"", volatileCount, text))
                }
            }
        } catch { logErr("RESULTS ERROR: \(error) type=\(type(of: error))") }
    }

    // analyzer 시작 (입력 시퀀스 연결)
    do { try await analyzer.start(inputSequence: stream) }
    catch { logErr("START ERROR: \(error)"); exit(3) }

    // 파일을 읽어 analyzer 포맷으로 변환, 청크로 실시간처럼 공급
    guard let file = try? AVAudioFile(forReading: url) else { logErr("open fail"); exit(4) }
    let srcFormat = file.processingFormat
    guard let converter = AVAudioConverter(from: srcFormat, to: analyzerFormat) else {
        logErr("converter fail"); exit(5)
    }
    let chunkFrames: AVAudioFrameCount = 1600   // ~0.1s @16kHz
    while true {
        guard let inBuf = AVAudioPCMBuffer(pcmFormat: srcFormat, frameCapacity: chunkFrames) else { break }
        do { try file.read(into: inBuf, frameCount: chunkFrames) } catch { break }
        if inBuf.frameLength == 0 { break }
        // 포맷 변환
        let ratio = analyzerFormat.sampleRate / srcFormat.sampleRate
        let outCap = AVAudioFrameCount(Double(inBuf.frameLength) * ratio + 64)
        guard let outBuf = AVAudioPCMBuffer(pcmFormat: analyzerFormat, frameCapacity: outCap) else { break }
        var err: NSError?
        var fed = false
        converter.convert(to: outBuf, error: &err) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true; status.pointee = .haveData; return inBuf
        }
        if let err { logErr("convert err: \(err)"); break }
        if outBuf.frameLength > 0 {
            continuation.yield(AnalyzerInput(buffer: outBuf))
        }
        try? await Task.sleep(nanoseconds: 100_000_000)  // 0.1s 실시간 시뮬레이션
    }
    continuation.finish()
    logErr("input finished; finalizing...")
    do { try await analyzer.finalizeAndFinishThroughEndOfInput() }
    catch { logErr("FINALIZE ERROR: \(error)") }

    await collector.value
    logErr("volatile=\(volatileCount) final=\(finalCount)")
    exit(0)
}

if #available(macOS 26.0, *) { await run() }
else { logErr("requires macOS 26"); exit(1) }
