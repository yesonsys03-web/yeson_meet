// swift-tools-version: 5.9
import PackageDescription

// Library + thin executable pattern:
// - Tests target the library (clean @testable import without main.swift side effects)
// - Executable is a thin shell that calls into the library
let package = Package(
    name: "YesonMacAudioHelper",
    platforms: [.macOS(.v14)],
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
    ]
)
