import XCTest
@testable import AppleTranslateKit

final class BatchTranslateTests: XCTestCase {
    func testParseInputArray() throws {
        let texts = try parseBatchInput(#"["Hello","World"]"#.data(using: .utf8)!)
        XCTAssertEqual(texts, ["Hello", "World"])
    }

    func testParseRejectsNonArray() {
        XCTAssertThrowsError(try parseBatchInput(#"{"a":1}"#.data(using: .utf8)!))
    }

    func testEncodeOutputArrayKeepsOrderAndHangul() throws {
        let out = try encodeBatchOutput(["안녕", "세계"])
        XCTAssertEqual(out, #"["안녕","세계"]"#)
    }

    func testRoundTripHandlesQuotesNewlinesAndKorean() throws {
        // Carry-forward from Task 2 review: embedded quotes/newlines must survive
        // parse -> encode round trip alongside Korean text.
        let originals = [
            "He said \"hello\"\nand left.",
            "안녕하세요\n\"반갑습니다\"",
        ]
        let encoded = try encodeBatchOutput(originals)
        let decoded = try parseBatchInput(encoded.data(using: .utf8)!)
        XCTAssertEqual(decoded, originals)
    }
}
