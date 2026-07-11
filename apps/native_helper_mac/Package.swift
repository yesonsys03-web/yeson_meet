// swift-tools-version: 5.9
import PackageDescription

// Library + thin executable pattern:
// - Tests target the library (clean @testable import without main.swift side effects)
// - Executable is a thin shell that calls into the library
let package = Package(
    name: "YesonMacAudioHelper",
    // Min macOS 14.2 (Sonoma): ScreenCaptureKit system-audio capture stability floor
    // per docs/NATIVE_DESKTOP_HELPER_PLAN.md §4.2/§9. 14.0–14.1 use BlackHole compat mode.
    platforms: [.macOS("14.2")],
    targets: [
        .target(
            name: "YesonMacAudioHelperKit",
            path: "Sources/YesonMacAudioHelperKit"
        ),
        .executableTarget(
            name: "YesonMacAudioHelper",
            dependencies: ["YesonMacAudioHelperKit"],
            path: "Sources/YesonMacAudioHelper"
        ),
        .testTarget(
            name: "YesonMacAudioHelperTests",
            dependencies: ["YesonMacAudioHelperKit"],
            path: "Tests/YesonMacAudioHelperTests"
        ),
        .target(
            name: "AppleTranslateKit",
            path: "Sources/AppleTranslateKit"
        ),
        .executableTarget(
            // 제품 바이너리명은 타깃명을 따르므로 실행 파일명을 타깃명으로 고정
            name: "apple-live-translate",
            dependencies: ["AppleTranslateKit"],
            path: "Sources/AppleTranslate"
        ),
        .testTarget(
            name: "AppleTranslateKitTests",
            dependencies: ["AppleTranslateKit"],
            path: "Tests/AppleTranslateKitTests"
        ),
    ]
)
