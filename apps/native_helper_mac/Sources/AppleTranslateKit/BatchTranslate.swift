import Foundation
import Translation

public func parseBatchInput(_ data: Data) throws -> [String] {
    guard let arr = try JSONSerialization.jsonObject(with: data) as? [Any] else {
        throw NSError(domain: "apple-translate", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "input must be a JSON array"])
    }
    return arr.map { "\($0)" }
}

public func encodeBatchOutput(_ texts: [String]) throws -> String {
    let data = try JSONSerialization.data(withJSONObject: texts, options: [.withoutEscapingSlashes])
    return String(data: data, encoding: .utf8)!
}

@available(macOS 15.0, *)
@MainActor
public func runBatchTranslate() async -> Int32 {
    do {
        let input = FileHandle.standardInput.readDataToEndOfFile()
        let texts = try parseBatchInput(input)
        let bridge = TranslatorBridge()
        let session = await bridge.acquireSession(
            source: .init(identifier: "en"), target: .init(identifier: "ko"))
        // Translation framework 배치 API — 순서 보존됨
        let requests = texts.map { TranslationSession.Request(sourceText: $0) }
        let responses = try await session.translations(from: requests)
        let out = try encodeBatchOutput(responses.map(\.targetText))
        FileHandle.standardOutput.write((out + "\n").data(using: .utf8)!)
        return 0
    } catch {
        FileHandle.standardError.write("translate-batch failed: \(error)\n".data(using: .utf8)!)
        return 1
    }
}
