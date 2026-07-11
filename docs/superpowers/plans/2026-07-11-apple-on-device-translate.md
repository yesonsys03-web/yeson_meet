# Apple 온디바이스 전사·번역 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실리콘맥 서버에서 Apple SpeechTranscriber(전사) + Translation framework(번역)를 라이브 미팅 프로바이더(`apple_live_translate`)와 자막메이커 전사/번역 엔진으로 선택 사용할 수 있게 한다.

**Architecture:** Swift 실행 파일 1개(`apple-live-translate`, 서브커맨드 `live`/`transcribe-file`/`translate-batch`)를 `native_helper_mac` SwiftPM에 새 타깃으로 추가하고, Python이 subprocess(JSONL stdin/stdout 프로토콜)로 사용한다. 라이브는 기존 `STTProvider` 체계, 자막메이커는 기존 `TranslationProvider` plug point와 `whisper_model="apple"` 센티널로 연결한다.

**Tech Stack:** Swift 5.9+/SwiftPM (SpeechAnalyzer·SpeechTranscriber, Translation framework, AppKit 숨김 윈도우), Python 3.12 (asyncio subprocess), FastAPI, Tauri 2.

**스펙:** `docs/superpowers/specs/2026-07-11-apple-on-device-translate-design.md`

## Global Constraints

- 기본 라이브 프로바이더는 `gemini_live_translate` 유지 — Apple은 **선택 옵션으로만** 추가.
- 게이팅: 번역(`translate-batch`)은 **macOS 15+**, 전사(`transcribe-file`)·라이브(`live`)는 **macOS 26+** (SpeechTranscriber). 모두 Apple Silicon(arm64) 전용. 가용성 체크는 기능별 분리.
- Swift 바이너리 경로 env: `YESON_APPLE_TRANSLATE_BIN`. 실행 파일명: `apple-live-translate`.
- DB 마이그레이션 금지 — `whisper_model`(String 32)에 `"apple"` 센티널 사용.
- 앵커 규칙 준수: 기존 파일 수정 시 앵커 경계 안에서 최소 패치. 파일 전체 재작성 금지.
- 새 Python 모듈은 `# === ANCHOR: <NAME>_START ===` / `_END` 앵커로 감싼다 (기존 파일 컨벤션).
- Python 테스트: `uv run pytest <파일> -v` (uv 미사용 환경이면 `pytest`). Swift 테스트: `swift test` (`apps/native_helper_mac`에서).
- Swift 빌드는 **Xcode 26 SDK가 설치된 실리콘맥에서만** 가능. Python 태스크는 어느 플랫폼에서든 진행 가능(테스트는 가짜 바이너리 사용).
- Live JSONL 이벤트 스키마 (스펙 §2): `{"type":"status","state":"ready"|"error","reason":...}`, `{"type":"partial","seq":N,"en":...,"ko":...}`, `{"type":"final","seq":N,"en":...,"ko":...,"t0":초,"t1":초}`.
- transcribe-file JSONL: `{"type":"token","t0":초,"t1":초,"text":단어}` 다수 + `{"type":"progress","frac":0.0~1.0}` + 마지막 `{"type":"done"}`.
- translate-batch: stdin에 JSON 배열(EN), stdout에 같은 길이 JSON 배열(KO), 실패 시 비 0 exit + stderr.

---

### Task 1: 스파이크 — Swift API 검증 (막히면 설계 재고)

**Files:**
- Create: `apps/native_helper_mac/scratch/spike-apple-translate/main.swift` (임시, 커밋은 노트만)
- Create: `docs/superpowers/specs/2026-07-11-apple-spike-notes.md`

**Interfaces:**
- Produces: 스파이크 노트 — (a) SpeechTranscriber 스트리밍/파일 전사에 실제 사용한 API 시그니처, (b) 헤드리스 TranslationSession 확보 코드 조각, (c) 각 기능의 최소 OS 확인 결과. Task 3~5의 Swift 코드는 이 노트의 확정 API로 조정한다.

- [ ] **Step 1: 검증 CLI 작성**

`swift package init` 없이 단일 파일 스크립트로 세 가지를 검증한다 (`swift main.swift`로 실행하거나 임시 SwiftPM 패키지 사용):

```swift
// 검증 1: 헤드리스 TranslationSession (숨김 윈도우 우회) — 최대 리스크
import AppKit
import SwiftUI
import Translation

@available(macOS 15.0, *)
final class TranslatorBridge {
    private var window: NSWindow?
    private var continuation: CheckedContinuation<TranslationSession, Never>?

    func acquireSession(source: Locale.Language, target: Locale.Language) async -> TranslationSession {
        await withCheckedContinuation { cont in
            self.continuation = cont
            DispatchQueue.main.async {
                let config = TranslationSession.Configuration(source: source, target: target)
                let host = NSHostingView(rootView:
                    Color.clear.translationTask(config) { session in
                        // session은 이 클로저 밖으로 escape 가능 — 클로저가 살아있는 동안 유효
                        cont.resume(returning: session)
                    })
                let win = NSWindow(contentRect: .init(x: 0, y: 0, width: 1, height: 1),
                                   styleMask: [.borderless], backing: .buffered, defer: false)
                win.contentView = host
                win.orderBack(nil)      // 화면에 보이지 않게
                win.alphaValue = 0
                self.window = win
            }
        }
    }
}

// main: NSApplication 없이 RunLoop만으로 되는지, NSApplication.shared 필요한지 확인
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)   // Dock 아이콘/포커스 탈취 금지
Task {
    let bridge = TranslatorBridge()
    let session = await bridge.acquireSession(
        source: .init(identifier: "en"), target: .init(identifier: "ko"))
    let responses = try await session.translations(from: [
        .init(sourceText: "Hello, this is a test."),
        .init(sourceText: "The animation timing looks off."),
    ])
    for r in responses { print("KO:", r.targetText) }
    exit(0)
}
app.run()
```

```swift
// 검증 2: SpeechTranscriber 파일 전사 + audioTimeRange (macOS 26+)
import Speech
if #available(macOS 26.0, *) {
    let transcriber = SpeechTranscriber(locale: Locale(identifier: "en_US"),
                                        transcriptionOptions: [],
                                        reportingOptions: [],
                                        attributeOptions: [.audioTimeRange])
    let analyzer = SpeechAnalyzer(modules: [transcriber])
    let file = try AVAudioFile(forReading: URL(fileURLWithPath: "/path/to/test.wav"))
    // analyzer에 파일 입력을 물리고 transcriber.results를 순회하며
    // AttributedString run의 .audioTimeRange 속성으로 단어별 CMTimeRange를 찍어본다
}
```

```swift
// 검증 3: SpeechTranscriber 스트리밍 — volatileResults 옵션으로 파셜 수신
// reportingOptions: [.volatileResults], AsyncStream<AnalyzerInput>으로 PCM 버퍼 공급,
// result.isFinal 플래그로 파셜/파이널 구분되는지 확인
```

- [ ] **Step 2: 실리콘맥에서 실행, 각 검증 결과 기록**

Run: `cd apps/native_helper_mac/scratch/spike-apple-translate && swift main.swift`
Expected: 검증 1 — KO 번역 2줄 출력 (언어팩 미설치면 에러 메시지 확인 후 시스템 설정에서 다운로드하고 재시도). 검증 2 — 단어별 타임레인지 출력. 검증 3 — 파셜→파이널 순서 출력.

- [ ] **Step 3: 스파이크 노트 작성**

`docs/superpowers/specs/2026-07-11-apple-spike-notes.md`에 기록: 실제 동작한 API 시그니처(플랜 코드와 다르면 차이 명시), NSApplication 필요 여부, 언어팩 미설치 시 던져지는 에러 타입, 전사 결과의 타임레인지 추출 코드. **검증 1이 어떤 우회로도 실패하면 여기서 멈추고 사용자와 설계 재논의.**

- [ ] **Step 4: Commit (노트만)**

```bash
git add docs/superpowers/specs/2026-07-11-apple-spike-notes.md
git commit -m "docs: Apple 온디바이스 스파이크 검증 노트"
```

---

### Task 2: Swift 패키지 타깃 + JSONL 이벤트 모델

**Files:**
- Modify: `apps/native_helper_mac/Package.swift`
- Create: `apps/native_helper_mac/Sources/AppleTranslateKit/Events.swift`
- Create: `apps/native_helper_mac/Sources/AppleTranslate/main.swift`
- Test: `apps/native_helper_mac/Tests/AppleTranslateKitTests/EventsTests.swift`

**Interfaces:**
- Produces: `enum OutEvent` (`.status(state:reason:)`, `.partial(seq:en:ko:)`, `.final(seq:en:ko:t0:t1:)`, `.token(t0:t1:text:)`, `.progress(frac:)`, `.done`) + `func jsonLine() -> String`. 실행 파일명 `apple-live-translate`. Task 3~5가 이 타입으로 stdout을 쓴다.

- [ ] **Step 1: Package.swift에 타깃 3개 추가**

기존 `targets` 배열에 추가 (기존 라이브러리+얇은 실행 파일 패턴 유지):

```swift
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
```

platforms는 `.macOS("14.2")` 유지 — 새 코드는 `@available(macOS 15.0/26.0, *)` 주석과 `#available` 런타임 체크로 게이팅한다 (기존 캡처 헬퍼의 최소 OS를 올리지 않기 위함).

- [ ] **Step 2: 실패하는 테스트 작성**

`Tests/AppleTranslateKitTests/EventsTests.swift`:

```swift
import XCTest
@testable import AppleTranslateKit

final class EventsTests: XCTestCase {
    func testPartialEncodesAsSingleJsonLine() throws {
        let line = OutEvent.partial(seq: 3, en: "Hello", ko: "안녕").jsonLine()
        XCTAssertFalse(line.contains("\n"))
        let obj = try JSONSerialization.jsonObject(
            with: line.data(using: .utf8)!) as! [String: Any]
        XCTAssertEqual(obj["type"] as? String, "partial")
        XCTAssertEqual(obj["seq"] as? Int, 3)
        XCTAssertEqual(obj["ko"] as? String, "안녕")
    }

    func testStatusErrorCarriesReason() throws {
        let line = OutEvent.status(state: "error", reason: "unsupported_os").jsonLine()
        let obj = try JSONSerialization.jsonObject(
            with: line.data(using: .utf8)!) as! [String: Any]
        XCTAssertEqual(obj["reason"] as? String, "unsupported_os")
    }

    func testFinalCarriesTimeRange() throws {
        let line = OutEvent.final(seq: 1, en: "Hi.", ko: "안녕.", t0: 1.25, t1: 2.5).jsonLine()
        let obj = try JSONSerialization.jsonObject(
            with: line.data(using: .utf8)!) as! [String: Any]
        XCTAssertEqual(obj["t0"] as? Double, 1.25)
    }
}
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `cd apps/native_helper_mac && swift test --filter EventsTests`
Expected: FAIL — `OutEvent` 미정의 컴파일 에러

- [ ] **Step 4: Events.swift 구현**

```swift
import Foundation

/// stdout JSONL 프로토콜의 이벤트. 한 이벤트 = 한 줄 (개행 없는 compact JSON).
public enum OutEvent {
    case status(state: String, reason: String?)
    case partial(seq: Int, en: String, ko: String)
    case `final`(seq: Int, en: String, ko: String, t0: Double, t1: Double)
    case token(t0: Double, t1: Double, text: String)
    case progress(frac: Double)
    case done

    public func jsonLine() -> String {
        var obj: [String: Any]
        switch self {
        case .status(let state, let reason):
            obj = ["type": "status", "state": state]
            if let reason { obj["reason"] = reason }
        case .partial(let seq, let en, let ko):
            obj = ["type": "partial", "seq": seq, "en": en, "ko": ko]
        case .final(let seq, let en, let ko, let t0, let t1):
            obj = ["type": "final", "seq": seq, "en": en, "ko": ko, "t0": t0, "t1": t1]
        case .token(let t0, let t1, let text):
            obj = ["type": "token", "t0": t0, "t1": t1, "text": text]
        case .progress(let frac):
            obj = ["type": "progress", "frac": frac]
        case .done:
            obj = ["type": "done"]
        }
        let data = try! JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
        return String(data: data, encoding: .utf8)!
    }
}

/// stdout에 이벤트 한 줄을 쓰고 즉시 flush (파이프 버퍼링 방지 — Python이 실시간 수신).
public func emit(_ event: OutEvent) {
    FileHandle.standardOutput.write((event.jsonLine() + "\n").data(using: .utf8)!)
}
```

`Sources/AppleTranslate/main.swift` (뼈대 — 서브커맨드는 Task 3~5에서 채움):

```swift
import AppleTranslateKit
import Foundation

let args = CommandLine.arguments
guard args.count >= 2 else {
    emit(.status(state: "error", reason: "usage: apple-live-translate <live|transcribe-file|translate-batch>"))
    exit(2)
}
switch args[1] {
case "live", "transcribe-file", "translate-batch":
    emit(.status(state: "error", reason: "not_implemented"))
    exit(1)
default:
    emit(.status(state: "error", reason: "unknown_subcommand"))
    exit(2)
}
```

- [ ] **Step 5: 테스트/빌드 통과 확인**

Run: `cd apps/native_helper_mac && swift test --filter EventsTests && swift build`
Expected: PASS + 빌드 성공 (`.build/debug/apple-live-translate` 생성)

- [ ] **Step 6: Commit**

```bash
git add apps/native_helper_mac/Package.swift apps/native_helper_mac/Sources/AppleTranslateKit apps/native_helper_mac/Sources/AppleTranslate apps/native_helper_mac/Tests/AppleTranslateKitTests
git commit -m "feat(mac): apple-live-translate 타깃 + JSONL 이벤트 모델"
```

---

### Task 3: Swift `translate-batch` 서브커맨드

**Files:**
- Create: `apps/native_helper_mac/Sources/AppleTranslateKit/TranslatorBridge.swift`
- Create: `apps/native_helper_mac/Sources/AppleTranslateKit/BatchTranslate.swift`
- Modify: `apps/native_helper_mac/Sources/AppleTranslate/main.swift`
- Test: `apps/native_helper_mac/Tests/AppleTranslateKitTests/BatchTranslateTests.swift`

**Interfaces:**
- Consumes: Task 2의 `OutEvent`/`emit`, Task 1 스파이크 노트의 확정 TranslatorBridge 코드.
- Produces: `TranslatorBridge.acquireSession(source:target:) async -> TranslationSession` (Task 5 live도 사용), `runBatchTranslate() async -> Int32` (exit code). stdin JSON 배열 → stdout JSON 배열 프로토콜.

- [ ] **Step 1: 실패하는 테스트 작성 (파싱 로직만 — 실번역은 실기 검증)**

```swift
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
}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd apps/native_helper_mac && swift test --filter BatchTranslateTests`
Expected: FAIL — `parseBatchInput` 미정의

- [ ] **Step 3: 구현**

`BatchTranslate.swift`:

```swift
import Foundation

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
```

`TranslatorBridge.swift`: Task 1 스파이크에서 확정한 숨김 윈도우 코드를 그대로 라이브러리화 (스파이크 노트의 코드가 플랜 예시와 다르면 노트를 따른다). `NSApplication.shared` + `setActivationPolicy(.prohibited)` 초기화는 main.swift에서 서브커맨드 실행 전에 1회 수행.

`main.swift`의 `translate-batch` 케이스 교체:

```swift
case "translate-batch":
    guard #available(macOS 15.0, *) else {
        emit(.status(state: "error", reason: "unsupported_os"))
        exit(3)
    }
    let app = NSApplication.shared
    app.setActivationPolicy(.prohibited)
    Task { exit(await runBatchTranslate()) }
    app.run()
```

- [ ] **Step 4: 테스트 통과 + 실기 스모크**

Run: `cd apps/native_helper_mac && swift test --filter BatchTranslateTests && swift build`
Expected: PASS

Run (실리콘맥, 언어팩 설치 상태): `echo '["Hello, nice to meet you.","The render is done."]' | .build/debug/apple-live-translate translate-batch`
Expected: `["만나서 반갑습니다.","렌더링이 완료되었습니다."]` 형태의 KO 2개 배열 (정확한 문구는 다를 수 있음 — 배열 길이 2 + 한국어인지 확인)

- [ ] **Step 5: Commit**

```bash
git add apps/native_helper_mac/Sources apps/native_helper_mac/Tests
git commit -m "feat(mac): translate-batch 서브커맨드 — 헤드리스 TranslationSession 배치 번역"
```

---

### Task 4: Swift `transcribe-file` 서브커맨드

**Files:**
- Create: `apps/native_helper_mac/Sources/AppleTranslateKit/FileTranscribe.swift`
- Modify: `apps/native_helper_mac/Sources/AppleTranslate/main.swift`

**Interfaces:**
- Consumes: Task 2 `OutEvent`/`emit`, Task 1 스파이크 노트의 SpeechTranscriber 파일 전사 API.
- Produces: `runTranscribeFile(path: String) async -> Int32`. stdout으로 `token`(단어별 t0/t1) + `progress` + `done` 방출 — Task 8의 Python이 이 토큰을 `words_to_cues`에 물린다.

- [ ] **Step 1: 구현 (이 태스크는 OS 프레임워크 직결이라 단위 테스트 대신 실기 스모크 — 로직이 얇음)**

`FileTranscribe.swift`:

```swift
import Foundation
import Speech
import AVFoundation

@available(macOS 26.0, *)
public func runTranscribeFile(path: String) async -> Int32 {
    do {
        let url = URL(fileURLWithPath: path)
        let file = try AVAudioFile(forReading: url)
        let durationSec = Double(file.length) / file.processingFormat.sampleRate

        let transcriber = SpeechTranscriber(
            locale: Locale(identifier: "en_US"),
            transcriptionOptions: [],
            reportingOptions: [],                    // 파일 전사는 파셜 불필요 — final만
            attributeOptions: [.audioTimeRange])
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        emit(.status(state: "ready", reason: nil))

        // 파일 입력 공급 + 결과 소비 (정확한 입력 API는 Task 1 스파이크 노트를 따름)
        try await analyzer.start(inputAudioFile: file)
        var lastT1 = 0.0
        for try await result in transcriber.results {
            for run in result.text.runs {
                guard let range = run.audioTimeRange else { continue }
                let text = String(result.text[run.range].characters)
                    .trimmingCharacters(in: .whitespaces)
                if text.isEmpty { continue }
                let t0 = range.start.seconds, t1 = range.end.seconds
                emit(.token(t0: t0, t1: t1, text: text))
                lastT1 = t1
            }
            if durationSec > 0 { emit(.progress(frac: min(lastT1 / durationSec, 1.0))) }
        }
        emit(.done)
        return 0
    } catch {
        emit(.status(state: "error", reason: "transcribe_failed: \(error)"))
        return 1
    }
}
```

`main.swift` 케이스 교체:

```swift
case "transcribe-file":
    guard #available(macOS 26.0, *) else {
        emit(.status(state: "error", reason: "unsupported_os"))
        exit(3)
    }
    guard args.count >= 4, args[2] == "--input" else {
        emit(.status(state: "error", reason: "usage: transcribe-file --input <wav>"))
        exit(2)
    }
    let path = args[3]
    Task { exit(await runTranscribeFile(path: path)) }
    RunLoop.main.run()
```

- [ ] **Step 2: 빌드 + 실기 스모크**

Run: `cd apps/native_helper_mac && swift build`
Expected: 빌드 성공

Run (실리콘맥): `.build/debug/apple-live-translate transcribe-file --input /path/to/test-en.wav`
Expected: `status ready` → `token` 여러 줄 (t0 < t1, 단조 증가) → `progress` → `done`. 90초 오디오가 수 초 내 완료되는지 확인 (속도가 이 기능의 존재 이유).

- [ ] **Step 3: Commit**

```bash
git add apps/native_helper_mac/Sources
git commit -m "feat(mac): transcribe-file 서브커맨드 — SpeechTranscriber 파일 전사"
```

---

### Task 5: Swift `live` 서브커맨드 (스트리밍 전사 + 스로틀 번역)

**Files:**
- Create: `apps/native_helper_mac/Sources/AppleTranslateKit/LiveEmitPolicy.swift`
- Create: `apps/native_helper_mac/Sources/AppleTranslateKit/LiveTranslate.swift`
- Modify: `apps/native_helper_mac/Sources/AppleTranslate/main.swift`
- Test: `apps/native_helper_mac/Tests/AppleTranslateKitTests/LiveEmitPolicyTests.swift`

**Interfaces:**
- Consumes: Task 2 `OutEvent`, Task 3 `TranslatorBridge`, 스파이크 노트의 스트리밍 API.
- Produces: `LiveEmitPolicy` — 순수 로직(스로틀/시퀀스), `runLive() async -> Int32`. stdin: 16kHz mono s16le PCM 스트림. stdout: `status`/`partial`/`final`.

- [ ] **Step 1: 실패하는 테스트 작성 (스로틀·시퀀스 로직)**

```swift
import XCTest
@testable import AppleTranslateKit

final class LiveEmitPolicyTests: XCTestCase {
    // policy.onVolatile(en:now:) -> en 스냅샷 번역이 허용되는 시점이면 그 텍스트, 아니면 nil
    // policy.onFinal(now:) -> 파이널 방출 후 seq 증가
    func testVolatileThrottledTo500ms() {
        var p = LiveEmitPolicy(throttleMs: 500, minDeltaChars: 4)
        XCTAssertEqual(p.onVolatile(en: "Hello there", now: 0.0), "Hello there")
        XCTAssertNil(p.onVolatile(en: "Hello there my", now: 0.3))       // 500ms 미경과
        XCTAssertEqual(p.onVolatile(en: "Hello there my friend", now: 0.6),
                       "Hello there my friend")
    }

    func testTinyDeltaIsSkipped() {
        var p = LiveEmitPolicy(throttleMs: 500, minDeltaChars: 4)
        _ = p.onVolatile(en: "Hello there", now: 0.0)
        XCTAssertNil(p.onVolatile(en: "Hello there!", now: 1.0))         // 델타 1자
    }

    func testSeqIncrementsOnFinal() {
        var p = LiveEmitPolicy(throttleMs: 500, minDeltaChars: 4)
        XCTAssertEqual(p.seq, 1)
        p.onFinal(now: 1.0)
        XCTAssertEqual(p.seq, 2)
        // 파이널 직후 볼래틸은 스로틀 리셋 — 새 발화 첫 파셜은 즉시 허용
        XCTAssertEqual(p.onVolatile(en: "Next one", now: 1.1), "Next one")
    }
}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd apps/native_helper_mac && swift test --filter LiveEmitPolicyTests`
Expected: FAIL — `LiveEmitPolicy` 미정의

- [ ] **Step 3: LiveEmitPolicy 구현**

```swift
import Foundation

/// 볼래틸(파셜) 결과의 번역 요청 빈도를 제어하는 순수 정책.
/// 번역은 로컬이라 빠르지만, 볼래틸은 프레임마다 갱신되므로 무스로틀이면 낭비.
public struct LiveEmitPolicy {
    public private(set) var seq = 1
    private let throttleSec: Double
    private let minDeltaChars: Int
    private var lastEmitAt: Double = -1e9
    private var lastEmittedEn = ""

    public init(throttleMs: Int, minDeltaChars: Int) {
        self.throttleSec = Double(throttleMs) / 1000.0
        self.minDeltaChars = minDeltaChars
    }

    public mutating func onVolatile(en: String, now: Double) -> String? {
        guard now - lastEmitAt >= throttleSec else { return nil }
        guard abs(en.count - lastEmittedEn.count) >= minDeltaChars || lastEmittedEn.isEmpty
        else { return nil }
        lastEmitAt = now
        lastEmittedEn = en
        return en
    }

    public mutating func onFinal(now: Double) {
        seq += 1
        lastEmitAt = -1e9          // 다음 발화 첫 파셜은 즉시 허용
        lastEmittedEn = ""
    }
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd apps/native_helper_mac && swift test --filter LiveEmitPolicyTests`
Expected: PASS

- [ ] **Step 5: runLive 구현**

`LiveTranslate.swift` — 구조 (정확한 SpeechAnalyzer 입력 API는 스파이크 노트를 따름):

```swift
import Foundation
import Speech
import AVFoundation

@available(macOS 26.0, *)
public func runLive() async -> Int32 {
    do {
        let transcriber = SpeechTranscriber(
            locale: Locale(identifier: "en_US"),
            transcriptionOptions: [],
            reportingOptions: [.volatileResults],
            attributeOptions: [.audioTimeRange])
        let analyzer = SpeechAnalyzer(modules: [transcriber])

        let bridge = TranslatorBridge()
        let session = await bridge.acquireSession(
            source: .init(identifier: "en"), target: .init(identifier: "ko"))
        emit(.status(state: "ready", reason: nil))

        // stdin 리더: 16kHz mono s16le → AVAudioPCMBuffer → analyzer 입력 스트림.
        // (analyzer에 스트림을 물리는 정확한 API는 스파이크 노트를 따름)
        let format = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000,
                                   channels: 1, interleaved: true)!
        let (inputStream, inputContinuation) = AsyncStream.makeStream(of: AnalyzerInput.self)
        let inputTask = Task {
            let stdin = FileHandle.standardInput
            while true {
                let data = stdin.readData(ofLength: 3200)   // 100ms @ 16kHz s16le mono
                if data.isEmpty { break }                    // EOF — sidecar 오디오 종료
                let frames = AVAudioFrameCount(data.count / 2)
                guard let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)
                else { continue }
                buf.frameLength = frames
                data.withUnsafeBytes { raw in
                    buf.int16ChannelData!.pointee.update(
                        from: raw.bindMemory(to: Int16.self).baseAddress!,
                        count: Int(frames))
                }
                inputContinuation.yield(AnalyzerInput(buffer: buf))
            }
            inputContinuation.finish()
        }
        try await analyzer.start(inputSequence: inputStream)

        var policy = LiveEmitPolicy(throttleMs: 500, minDeltaChars: 4)
        var utteranceStart = 0.0
        for try await result in transcriber.results {
            let en = String(result.text.characters).trimmingCharacters(in: .whitespaces)
            if en.isEmpty { continue }
            let now = ProcessInfo.processInfo.systemUptime
            if result.isFinal {
                let ko = try await translateOne(session, en)
                let t1 = lastAudioTime(of: result) ?? utteranceStart
                emit(.final(seq: policy.seq, en: en, ko: ko, t0: utteranceStart, t1: t1))
                policy.onFinal(now: now)
                utteranceStart = t1
            } else if let snapshot = policy.onVolatile(en: en, now: now) {
                let ko = try await translateOne(session, snapshot)
                emit(.partial(seq: policy.seq, en: snapshot, ko: ko))
            }
        }
        inputTask.cancel()
        return 0
    } catch {
        emit(.status(state: "error", reason: "live_failed: \(error)"))
        return 1
    }
}

@available(macOS 15.0, *)
private func translateOne(_ session: TranslationSession, _ text: String) async throws -> String {
    try await session.translations(from: [.init(sourceText: text)]).first?.targetText ?? text
}
```

`main.swift` 케이스 교체 (`translate-batch`와 동일하게 NSApplication 초기화 후 실행):

```swift
case "live":
    guard #available(macOS 26.0, *) else {
        emit(.status(state: "error", reason: "unsupported_os"))
        exit(3)
    }
    let app = NSApplication.shared
    app.setActivationPolicy(.prohibited)
    Task { exit(await runLive()) }
    app.run()
```

- [ ] **Step 6: 빌드 + 실기 스모크**

Run: `cd apps/native_helper_mac && swift build && swift test`
Expected: 전체 테스트 PASS

Run (실리콘맥): `ffmpeg -i test-en.wav -f s16le -ar 16000 -ac 1 - 2>/dev/null | .build/debug/apple-live-translate live`
Expected: `status ready` → `partial`(같은 seq로 자라는 en/ko) → `final`(t0/t1 포함) 반복. 말 끝나고 ~1초 내 final.

- [ ] **Step 7: Commit**

```bash
git add apps/native_helper_mac/Sources apps/native_helper_mac/Tests
git commit -m "feat(mac): live 서브커맨드 — 스트리밍 전사 + 500ms 스로틀 번역"
```

---

### Task 6: Python 공용 가용성 모듈 `apple_native.py`

**Files:**
- Create: `apps/server/ai/apple_native.py`
- Test: `apps/server/tests/test_apple_native.py`

**Interfaces:**
- Produces: `resolve_apple_bin() -> str | None`, `apple_mt_available() -> bool`, `apple_stt_available() -> bool`, 상수 `APPLE_BIN_ENV = "YESON_APPLE_TRANSLATE_BIN"`, `APPLE_TRANSCRIBE_MODEL = "apple"`. Task 7~10이 사용.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# === ANCHOR: TEST_APPLE_NATIVE_START ===
from __future__ import annotations

import stat

from apps.server.ai import apple_native


def _make_fake_bin(tmp_path):
    p = tmp_path / "apple-live-translate"
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


class TestResolveAppleBin:
    def test_env_var_wins(self, tmp_path, monkeypatch):
        p = _make_fake_bin(tmp_path)
        monkeypatch.setenv(apple_native.APPLE_BIN_ENV, str(p))
        assert apple_native.resolve_apple_bin() == str(p)

    def test_env_pointing_to_missing_file_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv(apple_native.APPLE_BIN_ENV, str(tmp_path / "nope"))
        monkeypatch.setattr(apple_native.shutil, "which", lambda name: None)
        assert apple_native.resolve_apple_bin() is None


class TestAvailability:
    def test_mt_needs_macos_15(self, tmp_path, monkeypatch):
        p = _make_fake_bin(tmp_path)
        monkeypatch.setenv(apple_native.APPLE_BIN_ENV, str(p))
        monkeypatch.setattr(apple_native, "_is_apple_silicon_mac", lambda: True)
        monkeypatch.setattr(apple_native, "_macos_major", lambda: 15)
        assert apple_native.apple_mt_available() is True
        assert apple_native.apple_stt_available() is False  # STT는 26 필요

    def test_stt_on_macos_26(self, tmp_path, monkeypatch):
        p = _make_fake_bin(tmp_path)
        monkeypatch.setenv(apple_native.APPLE_BIN_ENV, str(p))
        monkeypatch.setattr(apple_native, "_is_apple_silicon_mac", lambda: True)
        monkeypatch.setattr(apple_native, "_macos_major", lambda: 26)
        assert apple_native.apple_stt_available() is True

    def test_unavailable_off_mac(self, monkeypatch):
        monkeypatch.setattr(apple_native, "_is_apple_silicon_mac", lambda: False)
        assert apple_native.apple_mt_available() is False
        assert apple_native.apple_stt_available() is False
# === ANCHOR: TEST_APPLE_NATIVE_END ===
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest apps/server/tests/test_apple_native.py -v`
Expected: FAIL — `ModuleNotFoundError: apps.server.ai.apple_native`

- [ ] **Step 3: 구현**

```python
# === ANCHOR: APPLE_NATIVE_START ===
"""Apple 온디바이스 바이너리(apple-live-translate) 탐색 + 기능별 가용성.

게이팅은 기능별로 다르다 (스펙 §4.2): 번역(translate-batch)은 macOS 15+,
전사/라이브(SpeechTranscriber)는 macOS 26+. 모두 Apple Silicon 전용.
언어 에셋 유무 같은 깊은 체크는 여기서 하지 않는다 — 바이너리가 기동 시
status:error로 보고하고, 그 메시지가 운영자에게 표출된다.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys

APPLE_BIN_ENV = "YESON_APPLE_TRANSLATE_BIN"
# 자막메이커 whisper_model 필드에 넣는 센티널 — DB 스키마 변경 없이 엔진 선택.
APPLE_TRANSCRIBE_MODEL = "apple"
_BIN_NAME = "apple-live-translate"


def _is_apple_silicon_mac() -> bool:  # test seam
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _macos_major() -> int:  # test seam
    try:
        return int(platform.mac_ver()[0].split(".")[0])
    except (ValueError, IndexError):
        return 0


def resolve_apple_bin() -> str | None:
    """env 우선, 없으면 PATH. env가 없는 파일을 가리키면 무시하고 PATH 폴백."""
    env_path = os.environ.get(APPLE_BIN_ENV, "").strip()
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path
    return shutil.which(_BIN_NAME)


def apple_mt_available() -> bool:
    return (_is_apple_silicon_mac() and _macos_major() >= 15
            and resolve_apple_bin() is not None)


def apple_stt_available() -> bool:
    return (_is_apple_silicon_mac() and _macos_major() >= 26
            and resolve_apple_bin() is not None)
# === ANCHOR: APPLE_NATIVE_END ===
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest apps/server/tests/test_apple_native.py -v`
Expected: PASS (6개)

- [ ] **Step 5: Commit**

```bash
git add apps/server/ai/apple_native.py apps/server/tests/test_apple_native.py
git commit -m "feat(server): apple_native — Apple 바이너리 탐색 + 기능별 가용성 게이팅"
```

---

### Task 7: 자막메이커 번역 엔진 `AppleTranslator`

**Files:**
- Create: `apps/server/domain/video_captions/translate_apple.py`
- Modify: `apps/server/domain/video_captions/translate_cli.py` (`list_translate_engines`, `create_translator`)
- Test: `apps/server/tests/test_translate_apple.py`

**Interfaces:**
- Consumes: `apple_native.resolve_apple_bin/apple_mt_available` (Task 6), `TranslationError`/`TranslationProvider` (translate.py).
- Produces: `AppleTranslator(argv: list[str] | None = None, timeout: float = 120.0)` with `async translate_batch(texts: list[str]) -> list[str]`. `create_translator(provider="apple")`가 이를 반환. 엔진 value 문자열: `"apple"`.

참고: 글로서리 후보정은 `translate.py::translate_segments`가 모든 provider 출력에 이미 적용하므로 (line 130 `apply_ko_corrections`) 이 태스크에서 할 일 없음.

- [ ] **Step 1: 실패하는 테스트 작성**

가짜 바이너리는 python 스크립트로 만든다 (`argv` 테스트 심 사용 — CliTranslator와 같은 패턴):

```python
# === ANCHOR: TEST_TRANSLATE_APPLE_START ===
from __future__ import annotations

import sys
import textwrap

import pytest

from apps.server.domain.video_captions.translate import TranslationError
from apps.server.domain.video_captions.translate_apple import AppleTranslator


def _fake_bin(tmp_path, body: str):
    """stdin JSON 배열을 읽어 body 로직대로 응답하는 가짜 apple-live-translate."""
    script = tmp_path / "fake_apple.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


ECHO_KO = """\
    import json, sys
    texts = json.load(sys.stdin)
    print(json.dumps([f"KO:{t}" for t in texts], ensure_ascii=False))
"""

WRONG_LEN = """\
    import json, sys
    json.load(sys.stdin)
    print(json.dumps(["하나뿐"]))
"""

CRASH = """\
    import sys
    sys.stderr.write("boom: missing language asset\\n")
    sys.exit(1)
"""


class TestAppleTranslator:
    @pytest.mark.asyncio
    async def test_translates_batch_in_order(self, tmp_path):
        tr = AppleTranslator(argv=_fake_bin(tmp_path, ECHO_KO))
        out = await tr.translate_batch(["Hello", "World"])
        assert out == ["KO:Hello", "KO:World"]

    @pytest.mark.asyncio
    async def test_length_mismatch_raises(self, tmp_path):
        tr = AppleTranslator(argv=_fake_bin(tmp_path, WRONG_LEN))
        with pytest.raises(TranslationError, match="count mismatch"):
            await tr.translate_batch(["a", "b"])

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises_with_stderr(self, tmp_path):
        tr = AppleTranslator(argv=_fake_bin(tmp_path, CRASH))
        with pytest.raises(TranslationError, match="missing language asset"):
            await tr.translate_batch(["a"])

    @pytest.mark.asyncio
    async def test_empty_input_short_circuits(self, tmp_path):
        tr = AppleTranslator(argv=[sys.executable, "/nonexistent.py"])
        assert await tr.translate_batch([]) == []
# === ANCHOR: TEST_TRANSLATE_APPLE_END ===
```

주의: 기존 테스트가 `pytest.mark.asyncio`를 쓰는지 확인하고 (`grep -rn "asyncio" apps/server/tests/test_api_reports_admin.py` 등) 프로젝트의 async 테스트 컨벤션(예: `anyio`)을 따를 것. 컨벤션이 다르면 데코레이터만 바꾼다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest apps/server/tests/test_translate_apple.py -v`
Expected: FAIL — `ModuleNotFoundError: translate_apple`

- [ ] **Step 3: `translate_apple.py` 구현**

```python
# === ANCHOR: TRANSLATE_APPLE_START ===
"""Apple 온디바이스 배치 번역 (translate.py의 TranslationProvider plug point).

apple-live-translate translate-batch 서브커맨드에 JSON 배열을 stdin으로 주고
같은 길이의 KO 배열을 받는다. 로컬 NMT라 네트워크 왕복이 없어 배치가 초 단위로
끝난다. 프롬프트 주입이 불가하므로 용어 교정은 translate_segments의
apply_ko_corrections 후보정에 전적으로 의존한다.
"""
from __future__ import annotations

import asyncio
import json
import logging

from apps.server.ai.apple_native import resolve_apple_bin
from .translate import TranslationError

logger = logging.getLogger("yeson.video.translate_apple")

DEFAULT_TIMEOUT = 120.0


class AppleTranslator:
    """TranslationProvider backed by the on-device Apple Translation framework."""

    def __init__(self, argv: list[str] | None = None, timeout: float = DEFAULT_TIMEOUT):
        # argv는 테스트 심 — 운영에서는 resolve_apple_bin()으로 지연 해석
        self._argv = argv
        self._timeout = timeout

    def _resolved_argv(self) -> list[str]:
        if self._argv is not None:
            return list(self._argv)
        bin_path = resolve_apple_bin()
        if bin_path is None:
            raise TranslationError(
                "apple-live-translate 바이너리를 찾을 수 없습니다 "
                "(YESON_APPLE_TRANSLATE_BIN 또는 PATH 확인 — 실리콘맥 전용)")
        return [bin_path, "translate-batch"]

    async def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        argv = self._resolved_argv()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        payload = json.dumps(texts, ensure_ascii=False).encode("utf-8")
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            raise TranslationError(f"Apple 번역 시간 초과({self._timeout}s)") from exc
        if proc.returncode != 0:
            tail = stderr.decode("utf-8", errors="replace")[-300:]
            raise TranslationError(
                f"Apple 번역 실패 (returncode={proc.returncode}): {tail}")
        try:
            out = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise TranslationError(
                f"Apple 번역이 JSON이 아닌 출력 반환: {stdout[:200]!r}") from exc
        if not isinstance(out, list) or len(out) != len(texts):
            raise TranslationError(
                f"translation count mismatch: sent {len(texts)}, got "
                f"{len(out) if isinstance(out, list) else type(out).__name__}")
        return [str(t) for t in out]
# === ANCHOR: TRANSLATE_APPLE_END ===
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest apps/server/tests/test_translate_apple.py -v`
Expected: PASS (4개)

- [ ] **Step 5: `translate_cli.py` 배선 (최소 패치 2곳)**

`list_translate_engines()`의 리스트 마지막에 추가:

```python
        {"value": "apple", "label": "Apple 온디바이스 (실리콘맥, 초고속)",
         "available": apple_mt_available()},
```

파일 상단 import에 추가: `from apps.server.ai.apple_native import apple_mt_available`

`create_translator()`의 `if provider in ("", "gemini")` 블록 뒤에 추가:

```python
    if provider == "apple":
        from .translate_apple import AppleTranslator
        return AppleTranslator()
```

- [ ] **Step 6: 배선 테스트 추가 + 전체 통과 확인**

`test_translate_apple.py`에 추가:

```python
class TestWiring:
    def test_create_translator_apple(self):
        from apps.server.domain.video_captions.translate_cli import create_translator
        assert type(create_translator(provider="apple")).__name__ == "AppleTranslator"

    def test_engine_listed(self, monkeypatch):
        from apps.server.domain.video_captions import translate_cli
        monkeypatch.setattr(translate_cli, "apple_mt_available", lambda: True)
        engines = translate_cli.list_translate_engines()
        apple = [e for e in engines if e["value"] == "apple"]
        assert apple and apple[0]["available"] is True
```

Run: `uv run pytest apps/server/tests/test_translate_apple.py apps/server/tests/ -k "translate" -v`
Expected: 신규 6개 PASS + 기존 translate 테스트 회귀 없음

- [ ] **Step 7: Commit**

```bash
git add apps/server/domain/video_captions/translate_apple.py apps/server/domain/video_captions/translate_cli.py apps/server/tests/test_translate_apple.py
git commit -m "feat(video): Apple 온디바이스 번역 엔진 — TranslationProvider plug point 연결"
```

---

### Task 8: 자막메이커 Apple 전사 엔진

**Files:**
- Create: `apps/server/domain/video_captions/transcribe_apple.py`
- Modify: `apps/server/domain/video_captions/transcribe.py` (`transcribe_audio` 분기)
- Modify: `apps/server/api/v1/video_jobs.py` (`_require_model`)
- Modify: `apps/server/api/v1/video_models.py` (`list_video_models` 응답에 apple 항목)
- Test: `apps/server/tests/test_transcribe_apple.py`

**Interfaces:**
- Consumes: Task 6 `apple_native` (`APPLE_TRANSCRIBE_MODEL`, `apple_stt_available`, `resolve_apple_bin`), `transcribe.words_to_cues`/`StaleRunCancelled`, Task 4의 token/progress/done JSONL.
- Produces: `transcribe_audio_apple(audio_path: Path, progress_cb: Callable[[float], None] | None, argv: list[str] | None = None) -> list[SubSegment]` (blocking — pipeline이 기존처럼 `asyncio.to_thread`로 부름). `transcribe_audio(audio, "apple", cb)`가 이를 호출.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# === ANCHOR: TEST_TRANSCRIBE_APPLE_START ===
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from apps.server.domain.video_captions.transcribe import StaleRunCancelled
from apps.server.domain.video_captions.transcribe_apple import transcribe_audio_apple


def _fake_bin(tmp_path, body: str) -> list[str]:
    script = tmp_path / "fake_apple.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


TOKENS = """\
    import json
    events = [
        {"type": "status", "state": "ready"},
        {"type": "token", "t0": 0.0, "t1": 0.4, "text": "Hello"},
        {"type": "token", "t0": 0.5, "t1": 0.9, "text": "world."},
        {"type": "progress", "frac": 0.5},
        {"type": "token", "t0": 7.0, "t1": 7.4, "text": "Next"},
        {"type": "token", "t0": 7.5, "t1": 7.9, "text": "cue"},
        {"type": "progress", "frac": 1.0},
        {"type": "done"},
    ]
    for e in events:
        print(json.dumps(e))
"""

FAILS = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "error", "reason": "missing_stt_asset"}))
    sys.exit(1)
"""


class TestTranscribeAudioApple:
    def test_tokens_become_cues_via_words_to_cues(self, tmp_path):
        cues = transcribe_audio_apple(Path("unused.wav"), None,
                                      argv=_fake_bin(tmp_path, TOKENS))
        # 0.9→7.0 사이 6초 초과 갭 → words_to_cues가 두 큐로 분할
        assert len(cues) == 2
        assert cues[0].text == "Hello world."
        assert cues[0].start_ms == 0 and cues[0].end_ms == 900
        assert cues[1].seq == 2

    def test_progress_callback_invoked(self, tmp_path):
        seen: list[float] = []
        transcribe_audio_apple(Path("unused.wav"), seen.append,
                               argv=_fake_bin(tmp_path, TOKENS))
        assert seen == [0.5, 1.0]

    def test_stale_cancel_propagates_and_kills_proc(self, tmp_path):
        def cancel(_frac: float) -> None:
            raise StaleRunCancelled()
        with pytest.raises(StaleRunCancelled):
            transcribe_audio_apple(Path("unused.wav"), cancel,
                                   argv=_fake_bin(tmp_path, TOKENS))

    def test_binary_error_raises_runtime_error(self, tmp_path):
        with pytest.raises(RuntimeError, match="missing_stt_asset"):
            transcribe_audio_apple(Path("unused.wav"), None,
                                   argv=_fake_bin(tmp_path, FAILS))
# === ANCHOR: TEST_TRANSCRIBE_APPLE_END ===
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest apps/server/tests/test_transcribe_apple.py -v`
Expected: FAIL — `ModuleNotFoundError: transcribe_apple`

- [ ] **Step 3: `transcribe_apple.py` 구현**

```python
# === ANCHOR: TRANSCRIBE_APPLE_START ===
"""Apple SpeechTranscriber 파일 전사 (whisper_model="apple" 센티널 엔진).

apple-live-translate transcribe-file이 단어 단위 token JSONL을 방출하면,
faster-whisper의 Word와 같은 (.start/.end/.word) 모양으로 감싸 기존
words_to_cues(6초/90자 큐 분할)에 그대로 물린다. Blocking — 호출자는
transcribe.transcribe_audio를 통해 asyncio.to_thread로 부른다.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from apps.server.ai.apple_native import resolve_apple_bin
from .srt import SubSegment
from .transcribe import StaleRunCancelled, words_to_cues

logger = logging.getLogger("yeson.video.transcribe_apple")


@dataclass(frozen=True)
class _Token:
    start: float
    end: float
    word: str


def transcribe_audio_apple(
    audio_path: Path,
    progress_cb: Callable[[float], None] | None = None,
    argv: list[str] | None = None,
) -> list[SubSegment]:
    if argv is None:
        bin_path = resolve_apple_bin()
        if bin_path is None:
            raise RuntimeError(
                "apple-live-translate 바이너리를 찾을 수 없습니다 "
                "(YESON_APPLE_TRANSLATE_BIN 또는 PATH 확인)")
        argv = [bin_path, "transcribe-file", "--input", str(audio_path)]

    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    tokens: list[_Token] = []
    error_reason: str | None = None
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("transcribe_apple: non-JSON line skipped: %r", line[:120])
                continue
            etype = event.get("type")
            if etype == "token":
                # 공백 word는 words_to_cues가 스킵하므로 그대로 전달
                tokens.append(_Token(start=float(event["t0"]), end=float(event["t1"]),
                                     word=str(event.get("text", ""))))
            elif etype == "progress" and progress_cb is not None:
                progress_cb(float(event.get("frac", 0.0)))  # StaleRunCancelled 전파 가능
            elif etype == "status" and event.get("state") == "error":
                error_reason = str(event.get("reason", "unknown"))
            elif etype == "done":
                break
    except StaleRunCancelled:
        proc.kill()
        raise
    finally:
        proc.stdout and proc.stdout.close()
        rc = proc.wait(timeout=10)
    if error_reason is not None or rc != 0:
        stderr_tail = (proc.stderr.read() if proc.stderr else "")[-300:]
        raise RuntimeError(
            f"Apple 전사 실패: {error_reason or f'returncode={rc}'} {stderr_tail}")
    cues = words_to_cues(tokens)
    logger.info("transcribe_apple: %d cues from %d tokens", len(cues), len(tokens))
    return cues
# === ANCHOR: TRANSCRIBE_APPLE_END ===
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest apps/server/tests/test_transcribe_apple.py -v`
Expected: PASS (4개)

- [ ] **Step 5: `transcribe.py` 분기 (최소 패치 — 함수 첫머리, 로컬 import로 순환 방지)**

`transcribe_audio()` 본문 맨 앞(`if not is_downloaded(...)` 위)에 추가:

```python
    if model_name == "apple":
        # 모듈 단위 로컬 import (순환 방지) — 함수 직접 import 금지: 모듈 경유
        # 호출이어야 테스트에서 monkeypatch(transcribe_apple.transcribe_audio_apple)가 먹는다
        from . import transcribe_apple

        return transcribe_apple.transcribe_audio_apple(audio_path, progress_cb)
```

- [ ] **Step 6: `video_jobs.py::_require_model` 분기 + `video_models.py` 목록 항목**

`_require_model` 함수 첫머리에 추가 (기존 CATALOG 404 체크보다 먼저):

```python
    if name == APPLE_TRANSCRIBE_MODEL:
        if not apple_stt_available():
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Apple 온디바이스 전사는 실리콘맥(macOS 26+) 서버에서만 사용할 수 있습니다.")
        return
```

import 추가: `from apps.server.ai.apple_native import APPLE_TRANSCRIBE_MODEL, apple_stt_available`

`video_models.py::list_video_models` — 실리콘맥에서 드롭다운에 노출 (다운로드 불필요 항목):

```python
@router.get("")
async def list_video_models() -> dict:
    models = wm.list_models()
    if apple_stt_available():
        models.insert(0, {
            "name": APPLE_TRANSCRIBE_MODEL,
            "label": "Apple 온디바이스 (실리콘맥, 초고속)",
            "approx_bytes": 0, "downloaded": True, "disk_bytes": 0,
            "downloading": False, "progress": None,
            "builtin": True,  # 클라: 다운로드/삭제 버튼 숨김 플래그
        })
    return {"models": models}
```

import 추가: `from apps.server.ai.apple_native import APPLE_TRANSCRIBE_MODEL, apple_stt_available`

주의: `/{name}/download`·삭제 라우트는 `wm.CATALOG` 체크가 "apple"을 404로 거르므로 추가 방어 불필요.

- [ ] **Step 7: 배선 테스트 추가 + 통과 확인**

`test_transcribe_apple.py`에 추가:

```python
class TestWiring:
    def test_transcribe_audio_routes_to_apple(self, tmp_path, monkeypatch):
        from apps.server.domain.video_captions import transcribe, transcribe_apple
        called = {}
        monkeypatch.setattr(transcribe_apple, "transcribe_audio_apple",
                            lambda path, cb, argv=None: called.setdefault("hit", []) or [])
        assert transcribe.transcribe_audio(Path("x.wav"), "apple") == []
        assert "hit" in called

    def test_require_model_rejects_apple_when_unavailable(self, monkeypatch):
        from fastapi import HTTPException
        from apps.server.api.v1 import video_jobs
        monkeypatch.setattr(video_jobs, "apple_stt_available", lambda: False)
        with pytest.raises(HTTPException) as exc:
            video_jobs._require_model("apple")
        assert exc.value.status_code == 409
```

Run: `uv run pytest apps/server/tests/test_transcribe_apple.py apps/server/tests/ -k "video" -v`
Expected: 신규 6개 PASS + 기존 video 테스트 회귀 없음

- [ ] **Step 8: Commit**

```bash
git add apps/server/domain/video_captions/transcribe_apple.py apps/server/domain/video_captions/transcribe.py apps/server/api/v1/video_jobs.py apps/server/api/v1/video_models.py apps/server/tests/test_transcribe_apple.py
git commit -m "feat(video): Apple 온디바이스 전사 엔진 — whisper_model=apple 센티널"
```

---

### Task 9: 라이브 프로바이더 `AppleLiveTranslateProvider`

**Files:**
- Create: `apps/server/ai/apple_live_translate.py`
- Modify: `apps/server/ws/sidecar.py` (`create_ai_provider`)
- Modify: `apps/server/ai/live_session.py` (`_PERMANENT_ERROR_SIGNATURES`에 1개 추가)
- Test: `apps/server/tests/test_apple_live_translate.py`

**Interfaces:**
- Consumes: Task 2/5의 live JSONL 프로토콜, `TranslatedUtterance`/`STTProvider` (providers.py), `apply_ko_corrections` (glossary.py), Task 6 `resolve_apple_bin`.
- Produces: `AppleLiveTranslateProvider(argv: list[str] | None = None)` — `stream(audio, lang_hint) -> AsyncIterator[TranslatedUtterance]`. `create_ai_provider`가 `"apple_live_translate"`/`"apple"` 이름에 대해 반환. 영구 에러 예외 메시지에 `"provider unavailable"` 포함.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# === ANCHOR: TEST_APPLE_LIVE_TRANSLATE_START ===
from __future__ import annotations

import sys
import textwrap

import pytest

from apps.server.ai.apple_live_translate import AppleLiveTranslateProvider
from apps.server.ai.live_session import is_permanent_provider_error


def _fake_bin(tmp_path, body: str) -> list[str]:
    script = tmp_path / "fake_live.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


HAPPY = """\
    import json, sys
    # stdin은 무시 (오디오 소비 시늉만)
    for e in [
        {"type": "status", "state": "ready"},
        {"type": "partial", "seq": 1, "en": "Hello", "ko": "안녕"},
        {"type": "partial", "seq": 1, "en": "Hello there", "ko": "안녕하세요"},
        {"type": "final", "seq": 1, "en": "Hello there.", "ko": "안녕하세요.",
         "t0": 0.0, "t1": 1.5},
        {"type": "final", "seq": 2, "en": "Pencil test.", "ko": "연필 테스트.",
         "t0": 2.0, "t1": 3.0},
    ]:
        print(json.dumps(e, ensure_ascii=False), flush=True)
"""

UNAVAILABLE = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "error",
                      "reason": "unsupported_os"}), flush=True)
    sys.exit(3)
"""

CRASH = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    print(json.dumps({"type": "partial", "seq": 1, "en": "a", "ko": "아"}), flush=True)
    sys.exit(1)
"""


async def _empty_audio():
    yield b"\x00" * 640


async def _collect(provider):
    return [u async for u in provider.stream(_empty_audio(), "en")]


class TestAppleLiveTranslateProvider:
    @pytest.mark.asyncio
    async def test_partials_and_finals_mapped(self, tmp_path):
        provider = AppleLiveTranslateProvider(argv=_fake_bin(tmp_path, HAPPY))
        out = await _collect(provider)
        assert [(u.seq, u.is_final) for u in out] == [
            (1, False), (1, False), (1, True), (2, True)]
        assert out[2].text_en == "Hello there."
        assert out[0].provider_segment == 1

    @pytest.mark.asyncio
    async def test_ko_corrections_applied(self, tmp_path):
        provider = AppleLiveTranslateProvider(argv=_fake_bin(tmp_path, HAPPY))
        out = await _collect(provider)
        assert out[3].text_ko == "펜슬 테스트."  # 연필 → 펜슬 (glossary)

    @pytest.mark.asyncio
    async def test_status_error_is_permanent(self, tmp_path):
        provider = AppleLiveTranslateProvider(argv=_fake_bin(tmp_path, UNAVAILABLE))
        with pytest.raises(RuntimeError) as exc:
            await _collect(provider)
        assert is_permanent_provider_error(exc.value)

    @pytest.mark.asyncio
    async def test_crash_raises_transient_error(self, tmp_path):
        provider = AppleLiveTranslateProvider(argv=_fake_bin(tmp_path, CRASH))
        with pytest.raises(RuntimeError) as exc:
            await _collect(provider)
        assert not is_permanent_provider_error(exc.value)  # reconnect 대상

    @pytest.mark.asyncio
    async def test_provider_segment_increments_per_stream(self, tmp_path):
        provider = AppleLiveTranslateProvider(argv=_fake_bin(tmp_path, HAPPY))
        first = await _collect(provider)
        second = await _collect(provider)
        assert first[0].provider_segment == 1
        assert second[0].provider_segment == 2
# === ANCHOR: TEST_APPLE_LIVE_TRANSLATE_END ===
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest apps/server/tests/test_apple_live_translate.py -v`
Expected: FAIL — `ModuleNotFoundError: apple_live_translate`

- [ ] **Step 3: `apple_live_translate.py` 구현**

```python
# === ANCHOR: APPLE_LIVE_TRANSLATE_START ===
"""Apple 온디바이스 라이브 자막 프로바이더 (STTProvider 구현).

apple-live-translate live 서브커맨드를 subprocess로 띄워 stdin으로 16kHz mono
PCM을 펌핑하고 stdout JSONL(partial/final/status)을 TranslatedUtterance로
변환한다. 세션당 subprocess 1개: 크래시 시 예외가 live_session의 reconnect
루프로 전파되고, provider_segment가 stream() 호출마다 증가해
AISequenceNormalizer가 seq를 재정렬한다.

status:error(OS 미지원/에셋 없음)는 재시도해도 소용없는 영구 에러 —
메시지에 "provider unavailable"을 넣어 is_permanent_provider_error가
매칭되게 한다 (5분 백오프 + 운영자 알림 경로).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from apps.server.ai.apple_native import resolve_apple_bin
from apps.server.ai.glossary import apply_ko_corrections
from apps.server.ai.providers import TranslatedUtterance

logger = logging.getLogger("yeson.ai.apple_live_translate")


class AppleProviderUnavailable(RuntimeError):
    """영구 에러 — live_session의 signature 매칭용 문구를 메시지에 포함."""

    def __init__(self, reason: str):
        super().__init__(f"apple provider unavailable: {reason}")


class AppleLiveTranslateProvider:
    def __init__(self, argv: list[str] | None = None):
        self._argv = argv  # 테스트 심; None이면 스폰 시점에 해석
        self._segment = 0

    def _resolved_argv(self) -> list[str]:
        if self._argv is not None:
            return list(self._argv)
        bin_path = resolve_apple_bin()
        if bin_path is None:
            raise AppleProviderUnavailable("binary not found")
        return [bin_path, "live"]

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        lang_hint: str,
    ) -> AsyncIterator[TranslatedUtterance]:
        self._segment += 1
        segment = self._segment
        argv = self._resolved_argv()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        assert proc.stdin is not None and proc.stdout is not None

        async def _pump_audio() -> None:
            try:
                async for chunk in audio:
                    proc.stdin.write(chunk)
                    await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass  # 프로세스 사망은 stdout EOF 쪽에서 처리
            finally:
                with contextlib.suppress(Exception):
                    proc.stdin.close()

        pump = asyncio.create_task(_pump_audio())
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break  # EOF — 프로세스 종료
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("apple live: non-JSON line: %r", line[:120])
                    continue
                etype = event.get("type")
                if etype == "status":
                    if event.get("state") == "error":
                        raise AppleProviderUnavailable(str(event.get("reason", "unknown")))
                    continue
                if etype not in ("partial", "final"):
                    continue
                now = datetime.now(timezone.utc)
                yield TranslatedUtterance(
                    seq=int(event["seq"]),
                    text_en=str(event.get("en", "")),
                    text_ko=apply_ko_corrections(str(event.get("ko", "")).strip()),
                    started_at=now,
                    ended_at=now,
                    is_final=(etype == "final"),
                    provider_segment=segment,
                )
            rc = await proc.wait()
            if rc != 0:
                stderr_tail = (await proc.stderr.read()).decode(
                    "utf-8", errors="replace")[-300:]
                raise RuntimeError(f"apple live exited rc={rc}: {stderr_tail}")
        finally:
            pump.cancel()
            if proc.returncode is None:
                proc.kill()
# === ANCHOR: APPLE_LIVE_TRANSLATE_END ===
```

- [ ] **Step 4: `live_session.py` 영구 에러 signature 1줄 추가**

`_PERMANENT_ERROR_SIGNATURES` 튜플에 추가:

```python
    "provider unavailable",
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest apps/server/tests/test_apple_live_translate.py -v`
Expected: PASS (5개)

- [ ] **Step 6: `create_ai_provider` 배선 (sidecar.py 최소 패치)**

`create_ai_provider()`의 `gemini_live_translate` 분기 앞에 추가:

```python
    if provider_name in {"apple_live_translate", "apple"}:
        from apps.server.ai.apple_native import resolve_apple_bin

        if resolve_apple_bin() is None:
            return None  # 미지원 환경 — S2 count-only 모드 유지
        return AppleLiveTranslateProvider()
```

파일 상단 import에 추가: `from apps.server.ai.apple_live_translate import AppleLiveTranslateProvider`

배선 테스트를 `test_apple_live_translate.py`에 추가:

```python
class TestCreateProvider:
    def test_selected_when_bin_present(self, tmp_path, monkeypatch):
        from apps.server.ws import sidecar
        monkeypatch.setenv("YESON_AI_PROVIDER", "apple_live_translate")
        fake = tmp_path / "apple-live-translate"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        monkeypatch.setenv("YESON_APPLE_TRANSLATE_BIN", str(fake))
        provider = sidecar.create_ai_provider()
        assert type(provider).__name__ == "AppleLiveTranslateProvider"

    def test_none_when_bin_missing(self, monkeypatch):
        from apps.server.ws import sidecar
        monkeypatch.setenv("YESON_AI_PROVIDER", "apple_live_translate")
        monkeypatch.delenv("YESON_APPLE_TRANSLATE_BIN", raising=False)
        monkeypatch.setattr(
            "apps.server.ai.apple_native.shutil.which", lambda n: None)
        assert sidecar.create_ai_provider() is None
```

- [ ] **Step 7: 전체 서버 테스트 회귀 확인**

Run: `uv run pytest apps/server/tests/ -v`
Expected: 전부 PASS

- [ ] **Step 8: Commit**

```bash
git add apps/server/ai/apple_live_translate.py apps/server/ai/live_session.py apps/server/ws/sidecar.py apps/server/tests/test_apple_live_translate.py
git commit -m "feat(server): apple_live_translate 라이브 프로바이더 + 배선"
```

---

### Task 10: server_desktop 배선 (UI 옵션 + 바이너리 번들 + env 주입)

**Files:**
- Modify: `apps/server_desktop/src/setup/ServerConfigPanel.tsx` (PROVIDERS 배열)
- Modify: `apps/server_desktop/src-tauri/src/server_process.rs` (env 주입)
- Modify: `apps/server_desktop/src-tauri/tauri.conf.json` (resources)
- Create: `apps/native_helper_mac/scripts/build_apple_translate.sh`

**Interfaces:**
- Consumes: Task 2 실행 파일 `apple-live-translate`, Task 9의 프로바이더명 `"apple_live_translate"`, Task 6의 env 이름 `YESON_APPLE_TRANSLATE_BIN`.
- Produces: 번들 경로 `apps/server_desktop/src-tauri/binaries/apple-translate-aarch64-apple-darwin/apple-live-translate`; 서버 스폰 시 env 주입.

- [ ] **Step 1: 빌드 스크립트 작성**

`apps/native_helper_mac/scripts/build_apple_translate.sh` (기존 `scripts/` 컨벤션 확인 후 맞출 것):

```bash
#!/usr/bin/env bash
# apple-live-translate를 release 빌드해 server_desktop 번들 위치로 복사.
# 실리콘맥 + Xcode 26 SDK 전용 — 다른 플랫폼 빌드에서는 호출하지 않는다.
set -euo pipefail
cd "$(dirname "$0")/.."
swift build -c release --product apple-live-translate
DEST="../server_desktop/src-tauri/binaries/apple-translate-aarch64-apple-darwin"
mkdir -p "$DEST"
cp ".build/release/apple-live-translate" "$DEST/"
echo "copied → $DEST/apple-live-translate"
```

Run: `chmod +x apps/native_helper_mac/scripts/build_apple_translate.sh && apps/native_helper_mac/scripts/build_apple_translate.sh`
Expected: 바이너리 복사 완료 메시지

- [ ] **Step 2: tauri.conf.json resources 추가**

`"resources"` 배열에 추가 (기존 ffmpeg 패턴과 동일):

```json
"binaries/apple-translate-*/**/*"
```

주의: 매칭되는 파일이 없어도 Tauri 빌드가 깨지지 않는지 Windows 빌드로 확인 — 깨지면 mac 전용 conf 오버레이(`tauri.macos.conf.json`)로 옮긴다.

- [ ] **Step 3: server_process.rs env 주입**

기존 `.env("YESON_AI_PROVIDER", &provider)` (약 line 290) 근처에, ffmpeg/cloudflared 번들 경로를 해석하는 기존 패턴을 그대로 따라 추가:

```rust
// Apple 온디바이스 번역 바이너리 (실리콘맥 번들에만 존재; 없으면 env 미주입 —
// 서버는 PATH 폴백 후 가용성 게이팅으로 처리)
if let Some(apple_bin) = resolve_bundled_binary(&app, "apple-translate", "apple-live-translate") {
    cmd = cmd.env("YESON_APPLE_TRANSLATE_BIN", apple_bin);
}
```

`resolve_bundled_binary`는 기존 ffmpeg 경로 해석 헬퍼를 지칭 — 실제 함수명/시그니처는 `server_process.rs`에서 ffmpeg 해석 코드를 읽고 동일 패턴으로 맞출 것 (헬퍼가 없으면 ffmpeg 해석 코드를 최소한으로 일반화).

- [ ] **Step 4: ServerConfigPanel.tsx PROVIDERS 추가**

`const PROVIDERS = [...]` 배열에 `"apple_live_translate"` 추가. 드롭다운 라벨이 배열 값 그대로면 그대로 두고, 라벨 매핑이 있으면 `"Apple 온디바이스 (실리콘맥 전용)"` 추가. 비(非)실리콘맥 서버에서 선택하면 서버가 count-only 모드로 뜨므로, 옵션 라벨에 "(실리콘맥 전용)"을 반드시 포함해 오선택을 줄인다.

- [ ] **Step 5: 데스크톱 빌드 + 수동 확인**

Run: `cd apps/server_desktop && pnpm install && pnpm tauri build --debug` (실리콘맥)
Expected: 빌드 성공. 앱 실행 → 설정에서 provider `apple_live_translate` 선택 → 서버 시작 → 로그에 `YESON_APPLE_TRANSLATE_BIN` 주입 확인.

- [ ] **Step 6: Commit**

```bash
git add apps/native_helper_mac/scripts/build_apple_translate.sh apps/server_desktop/src-tauri/tauri.conf.json apps/server_desktop/src-tauri/src/server_process.rs apps/server_desktop/src/setup/ServerConfigPanel.tsx
git commit -m "feat(desktop): Apple 프로바이더 옵션 + 바이너리 번들/env 주입"
```

---

### Task 11: 실기 종단 검증 + 성능 실측

**Files:**
- Modify: `docs/superpowers/specs/2026-07-11-apple-spike-notes.md` (실측 결과 추가)

**Interfaces:**
- Consumes: Task 1~10 전부 완료 상태, 실리콘맥(macOS 26, 언어팩 설치).

- [ ] **Step 1: 자막메이커 종단 확인**

앱에서 영상 1개 업로드 → 전사 모델 "Apple 온디바이스" + 번역 엔진 "Apple 온디바이스" 선택 → 완료까지 소요 시간 기록. 같은 영상을 whisper small + Gemini로 재실행해 비교 (전사/번역 단계별).

- [ ] **Step 2: 라이브 종단 확인**

provider `apple_live_translate`로 서버 시작 → sidecar에서 synthetic 영어 오디오 전송 (`docs/ARCHITECTURE.md`의 S3 local synthetic 절차 참고) → viewer에서 파셜/파이널 수신 확인, phrase-end→first subtitle 레이턴시 기록 (Gemini 실측 P50 1419.8ms와 비교).

- [ ] **Step 3: 엣지 확인 2개**

(a) 언어팩 삭제 상태에서 라이브 시작 → 운영자 화면에 provider_error 표출 + 5분 백오프 확인. (b) 라이브 중 `kill -9 <apple-live-translate pid>` → 자동 재접속(reconnect) + 자막 seq 연속성 확인.

- [ ] **Step 4: 실측 결과를 스파이크 노트에 추가 + Commit**

```bash
git add docs/superpowers/specs/2026-07-11-apple-spike-notes.md
git commit -m "docs: Apple 온디바이스 실기 검증·성능 실측 결과"
```

---

## Self-Review 체크 결과 (작성 시 수행)

- 스펙 §3(라이브)→Task 5·9, §4.1(전사)→Task 4·8, §4.2(번역)→Task 3·7, §4.3(조합)→기존 API가 이미 독립 선택 지원(추가 작업 없음 확인), §5(에러)→Task 8·9·11, §6(테스트)→각 태스크 + Task 11, §7(스파이크 우선)→Task 1. 게이팅 분리(15 vs 26)→Task 6.
- Swift API 시그니처(SpeechAnalyzer 입력 공급 등)는 OS 신규 API라 플랜 코드가 근사치임 — Task 1 스파이크 노트가 확정본이며 각 Swift 태스크에 해당 참조를 명시했다.
