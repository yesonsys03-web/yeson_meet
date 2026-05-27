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

    public func start(frameHandler: @escaping (Data) -> Void) throws {
        self.frameHandler = frameHandler
        Task {
            do {
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
                try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: .global())
                try await stream.startCapture()
                self.stream = stream
            } catch {
                FileHandle.standardError.write(
                    "screencapturekit_start_error: \(error)\n".data(using: .utf8) ?? Data()
                )
            }
        }
    }

    public func stop() {
        Task {
            try? await stream?.stopCapture()
            stream = nil
        }
    }

    public func dispose() {
        stop()
        converter = nil
        frameHandler = nil
    }

    // MARK: SCStreamOutput
    public func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio,
              CMSampleBufferIsValid(sampleBuffer),
              let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }

        var totalLength = 0
        var dataPointer: UnsafeMutablePointer<Int8>?
        CMBlockBufferGetDataPointer(blockBuffer, atOffset: 0, lengthAtOffsetOut: nil,
                                    totalLengthOut: &totalLength, dataPointerOut: &dataPointer)
        guard let ptr = dataPointer else { return }
        // 2 channels × float32 (4 bytes) = 8 bytes/frame
        let frameCount = AVAudioFrameCount(totalLength / 8)
        if frameCount == 0 { return }
        var floats = [Float](repeating: 0, count: totalLength / 4)
        memcpy(&floats, ptr, totalLength)

        do {
            let converted = try converter?.process(floats: &floats, frameCount: frameCount) ?? Data()
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
