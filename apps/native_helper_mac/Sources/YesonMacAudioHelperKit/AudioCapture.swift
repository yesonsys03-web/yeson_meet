import Foundation

// === ANCHOR: AUDIO_CAPTURE_START ===
public enum PermissionStatus: String, Encodable {
    case granted, denied, notDetermined, restricted, notApplicable
}

public enum CaptureTarget {
    case systemDefault
    case device(String)
    case app(String)  // bundle_id
}

public enum CaptureError: Error {
    case permissionDenied
    case unsupportedOS
    case deviceNotFound
    case internalError(String)
}

public protocol AudioCapture {
    var permissionStatus: PermissionStatus { get }
    func requestPermission() async -> PermissionStatus
    func setTarget(_ target: CaptureTarget) throws
    func listTargets() -> [CaptureTarget]

    /// Start capture. Subsequent PCM frames flow to `frameHandler`.
    /// frameHandler is invoked with already-converted 16 kHz mono Int16 LE Data of size 640 bytes (20 ms).
    func start(frameHandler: @escaping (Data) -> Void) throws
    func stop()
    func dispose()
}

/// Constants — every implementation must honor.
public enum AudioContract {
    public static let sampleRate: Int = 16_000
    public static let channels: Int = 1
    public static let frameMs: Int = 20
    public static let frameBytes: Int = 640 // 320 samples * 2 bytes
}
// === ANCHOR: AUDIO_CAPTURE_END ===
