import Foundation
import AVFoundation

// === ANCHOR: PCM_CONVERTER_START ===
/// Resamples float32 **non-interleaved (planar)** input to 16 kHz mono Int16 little-endian.
/// Source layout matches ScreenCaptureKit audio (verified live: nonInterleaved=true).
public final class PCMConverter {
    private let converter: AVAudioConverter
    private let sourceFormat: AVAudioFormat
    private let targetFormat: AVAudioFormat
    private let sourceChannels: UInt32

    public init(sourceSampleRate: Double, sourceChannels: UInt32) {
        self.sourceChannels = sourceChannels
        guard let src = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: sourceSampleRate,
            channels: sourceChannels,
            interleaved: false
        ) else {
            fatalError("source format invalid: sr=\(sourceSampleRate) ch=\(sourceChannels)")
        }
        guard let dst = AVAudioFormat(
            commonFormat: .pcmFormatInt16,
            sampleRate: 16000,
            channels: 1,
            interleaved: true
        ) else {
            fatalError("target format invalid")
        }
        self.sourceFormat = src
        self.targetFormat = dst
        guard let conv = AVAudioConverter(from: src, to: dst) else {
            fatalError("AVAudioConverter init failed")
        }
        self.converter = conv
    }

    /// Process N source frames of **channel-major planar** float32 and return s16le LE bytes
    /// at 16 kHz mono. `planarFloats` is laid out `[ch0(frameCount) | ch1(frameCount) | ...]`
    /// and must contain at least `frameCount * sourceChannels` samples.
    /// Loops until the resampler flushes its filter tail (one process() = one shot).
    public func process(planarFloats: inout [Float], frameCount: AVAudioFrameCount) throws -> Data {
        if frameCount == 0 { return Data() }
        guard let inBuf = AVAudioPCMBuffer(pcmFormat: sourceFormat, frameCapacity: frameCount) else {
            throw NSError(domain: "PCMConverter", code: 1)
        }
        inBuf.frameLength = frameCount
        let ch = Int(sourceChannels)
        let n = Int(frameCount)
        // Non-interleaved buffer: copy each channel slice into its own channel pointer.
        planarFloats.withUnsafeBufferPointer { ptr in
            guard let chans = inBuf.floatChannelData, let src = ptr.baseAddress else { return }
            for c in 0..<ch {
                chans[c].update(from: src + c * n, count: n)
            }
        }

        // Step buffer sized generously to drain the resampler in 1-2 convert calls.
        let stepCapacity = AVAudioFrameCount(
            Double(frameCount) * 16000.0 / sourceFormat.sampleRate + 64
        )
        var consumed = false
        var collected = Data()

        while true {
            guard let outBuf = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: stepCapacity) else {
                throw NSError(domain: "PCMConverter", code: 2)
            }
            var error: NSError?
            let status = converter.convert(to: outBuf, error: &error) { _, inputStatus in
                if consumed {
                    inputStatus.pointee = .noDataNow
                    return nil
                }
                consumed = true
                inputStatus.pointee = .haveData
                return inBuf
            }
            if let error = error { throw error }
            if status == .error {
                throw NSError(domain: "PCMConverter", code: 3)
            }
            let samples = Int(outBuf.frameLength)
            if samples > 0, let data = outBuf.int16ChannelData?[0] {
                collected.append(Data(bytes: data, count: samples * 2))
            }
            if status == .endOfStream { break }
            if samples == 0 { break }
        }
        return collected
    }
}
// === ANCHOR: PCM_CONVERTER_END ===
