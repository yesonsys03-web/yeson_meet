import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreGraphics
import CoreMedia

// === ANCHOR: SCK_PROVIDER_START ===
public final class ScreenCaptureKitProvider: NSObject, AudioCapture, SCStreamOutput {
    private var stream: SCStream?
    private var converter: PCMConverter?
    private var frameHandler: ((Data) -> Void)?
    private var target: CaptureTarget = .systemDefault
    private var pending = Data()
    // Serial queue for SCStream audio callbacks: serializes `pending` mutation so the
    // append + 640-byte drain loop is race-free (a concurrent queue here is a data race).
    private let sampleQueue = DispatchQueue(label: "dev.yeson.audio.sample")
    private var loggedFormat = false

    public override init() {
        super.init()
    }

    public var permissionStatus: PermissionStatus {
        // ScreenCaptureKit needs Screen Recording permission for system audio.
        CGPreflightScreenCaptureAccess() ? .granted : .notDetermined
    }

    public func requestPermission() async -> PermissionStatus {
        let ok = CGRequestScreenCaptureAccess()
        return ok ? .granted : .denied
    }

    public func setTarget(_ target: CaptureTarget) throws {
        self.target = target
    }

    public func listTargets() -> [CaptureTarget] {
        return [.systemDefault]  // PoC scope: system default only
    }

    public func start(frameHandler: @escaping (Data) -> Void) async throws {
        self.frameHandler = frameHandler
        let shareable = try await SCShareableContent.excludingDesktopWindows(
            false, onScreenWindowsOnly: false
        )
        guard let display = shareable.displays.first else {
            throw CaptureError.deviceNotFound
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48000
        config.channelCount = 2
        // Suppress video (audio is what matters): tiny frame at 1 fps.
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.width = 2
        config.height = 2

        self.converter = PCMConverter(sourceSampleRate: 48000, sourceChannels: 2)

        let stream = SCStream(filter: filter, configuration: config, delegate: nil)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: sampleQueue)
        // Await actual startup so failures propagate to the caller (no premature "started").
        try await stream.startCapture()
        self.stream = stream
    }

    public func stop() {
        guard let stream = stream else { return }
        self.stream = nil
        // Wait for ScreenCaptureKit to fully release the system audio tap before
        // returning (the caller exits right after). A fire-and-forget Task let the
        // process exit mid-teardown, leaving the tap dirty so a helper restarted
        // immediately after inherited a *silent* stream ("오디오 없음"). Bounded so
        // a stuck stopCapture can't outlast the parent's SIGKILL backstop.
        let done = DispatchSemaphore(value: 0)
        stream.stopCapture { _ in done.signal() }
        _ = done.wait(timeout: .now() + 2.0)
    }

    public func dispose() {
        stop()
        converter = nil
        frameHandler = nil
    }

    // MARK: SCStreamOutput
    public func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, CMSampleBufferIsValid(sampleBuffer) else { return }

        // One-time format probe (Task 24 verification): runs BEFORE the data-buffer guard so a
        // planar / non-contiguous buffer (CMSampleBufferGetDataBuffer == nil) is still characterized.
        // SCStream delivers non-interleaved (channel-major planar) float32 — confirmed live —
        // and PCMConverter is configured non-interleaved to match. `hasDataBuffer=false` would mean
        // the contiguous-block path below can't read it (would need the AudioBufferList API instead).
        if !loggedFormat {
            loggedFormat = true
            let hasDataBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) != nil
            if let fmt = CMSampleBufferGetFormatDescription(sampleBuffer),
               let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(fmt)?.pointee {
                let nonInterleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0
                FileHandle.standardError.write(
                    ("audio_format_check: sampleRate=\(asbd.mSampleRate) channels=\(asbd.mChannelsPerFrame)"
                     + " bitsPerChannel=\(asbd.mBitsPerChannel) nonInterleaved=\(nonInterleaved)"
                     + " hasDataBuffer=\(hasDataBuffer)\n")
                        .data(using: .utf8) ?? Data()
                )
            }
        }

        guard let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }

        var totalLength = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        CMBlockBufferGetDataPointer(blockBuffer, atOffset: 0, lengthAtOffsetOut: nil,
                                    totalLengthOut: &totalLength, dataPointerOut: &dataPointer)
        guard let ptr = dataPointer else { return }
        // 2 channels × float32 (4 bytes) = 8 bytes/frame → frames per channel.
        let frameCount = AVAudioFrameCount(totalLength / 8)
        if frameCount == 0 { return }
        // Planar contiguous layout [ch0(frameCount) | ch1(frameCount)] — channel-major,
        // which is exactly the input contract PCMConverter.process(planarFloats:) expects.
        var floats = [Float](repeating: 0, count: totalLength / 4)
        memcpy(&floats, ptr, totalLength)

        do {
            let converted = try converter?.process(planarFloats: &floats, frameCount: frameCount) ?? Data()
            pending.append(converted)
            // Emit in 640-byte frames
            while pending.count >= AudioContract.frameBytes {
                let chunk = pending.prefix(AudioContract.frameBytes)
                pending.removeFirst(AudioContract.frameBytes)
                frameHandler?(Data(chunk))
            }
        } catch {
            FileHandle.standardError.write(
                "conversion_error: \(error)\n".data(using: .utf8) ?? Data()
            )
        }
    }
}
// === ANCHOR: SCK_PROVIDER_END ===
