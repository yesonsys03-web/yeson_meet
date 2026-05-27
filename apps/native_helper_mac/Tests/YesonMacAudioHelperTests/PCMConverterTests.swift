import XCTest
import YesonMacAudioHelperKit

final class PCMConverterTests: XCTestCase {
    func testConvertsFloat48kStereoTo16kMonoS16LE() throws {
        let conv = PCMConverter(sourceSampleRate: 48000, sourceChannels: 2)
        // 1 second of stereo 48k at 0.5 amplitude
        let samples = 48000 * 2
        var input = [Float](repeating: 0.5, count: samples)
        let output = try conv.process(floats: &input, frameCount: 48000)
        // Single-shot resample of 48k → 16k drops ~60ms of filter tail (re-emerges
        // on subsequent calls). Helper feeds audio continuously, so this only
        // shows at shutdown — accept 90-105% of nominal here, verify amplitude tightly.
        let nominalBytes = 16000 * 2
        XCTAssertGreaterThan(output.count, Int(Double(nominalBytes) * 0.90))
        XCTAssertLessThan(output.count, Int(Double(nominalBytes) * 1.05))
        // Probe a stable mid-signal sample (1000th frame): amplitude 0.5 → ~16384.
        let probeOffset = 1000 * 2
        let s = output.withUnsafeBytes { $0.load(fromByteOffset: probeOffset, as: Int16.self) }
        XCTAssertEqual(Int(Int16(littleEndian: s)), 16384, accuracy: 100)
    }

    func testStreamingTotalConverges() throws {
        // Feed in 20ms chunks (960 input frames at 48k) and accumulate.
        // After 1 second of input (50 chunks), total output should be ~16000 frames.
        let conv = PCMConverter(sourceSampleRate: 48000, sourceChannels: 2)
        let chunkInputFrames: UInt32 = 960
        let chunkSamples = Int(chunkInputFrames) * 2 // stereo
        var input = [Float](repeating: 0.5, count: chunkSamples)
        var total = 0
        for _ in 0..<50 {
            let out = try conv.process(floats: &input, frameCount: chunkInputFrames)
            total += out.count
        }
        // 50 chunks × 20ms = 1 s. Streaming behavior should converge near 16000 frames.
        // Allow ±10% for cumulative filter delay (≈1-2 chunks worth).
        let nominalBytes = 16000 * 2
        XCTAssertGreaterThan(total, Int(Double(nominalBytes) * 0.85))
        XCTAssertLessThan(total, Int(Double(nominalBytes) * 1.05))
    }

    func testEmitsZeroBytesForEmptyInput() throws {
        let conv = PCMConverter(sourceSampleRate: 48000, sourceChannels: 2)
        var input: [Float] = []
        let output = try conv.process(floats: &input, frameCount: 0)
        XCTAssertEqual(output.count, 0)
    }
}
