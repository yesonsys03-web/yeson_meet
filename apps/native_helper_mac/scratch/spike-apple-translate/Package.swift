// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "spike-apple-translate",
    platforms: [.macOS("26.0")],
    targets: [
        .executableTarget(name: "verify1", path: "Sources/verify1",
                          swiftSettings: [.swiftLanguageMode(.v5)]),
        .executableTarget(name: "verify2", path: "Sources/verify2",
                          swiftSettings: [.swiftLanguageMode(.v5)]),
        .executableTarget(name: "verify3", path: "Sources/verify3",
                          swiftSettings: [.swiftLanguageMode(.v5)]),
    ]
)
