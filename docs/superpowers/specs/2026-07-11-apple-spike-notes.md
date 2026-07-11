# Apple 온디바이스 전사·번역 스파이크 검증 노트

- 날짜: 2026-07-11
- 검증 머신: Apple Silicon, macOS 26.4.1 (25E253), Xcode 26.5 (17F42), Swift 6.3.2
- 관련 설계: `docs/superpowers/specs/2026-07-11-apple-on-device-translate-design.md`
- 스크래치 코드(커밋 안 함): `apps/native_helper_mac/scratch/spike-apple-translate/`
- 상태: **3개 검증 전부 PASS**. Task 3~5의 Swift 코드는 이 문서의 확정 API를 플랜의 근사 코드보다 우선한다.

## 요약 (결론 먼저)

| 검증 | 결과 | 핵심 |
|---|---|---|
| 1. 헤드리스 TranslationSession | ✅ PASS | 숨김 NSWindow + NSHostingView + `.translationTask`로 CLI에서 세션 확보·번역 성공. **NSApplication.shared + `app.run()` 필요** (RunLoop 단독 불가). EN→KO 팩은 이미 설치돼 있었음. |
| 2. SpeechTranscriber 파일 전사 | ✅ PASS | `SpeechAnalyzer.analyzeSequence(from: AVAudioFile)`로 전사, run별 `audioTimeRange`(CMTimeRange) 추출. **en-US 모델 미설치였으나 `AssetInventory`로 프로그램적 다운로드 성공(~20s), 시스템 설정 UI 불필요**. 파일 분석 ~11x 실시간. |
| 3. SpeechTranscriber 스트리밍 | ✅ PASS | `reportingOptions: [.volatileResults]` + `AsyncStream<AnalyzerInput>` + `analyzer.start(inputSequence:)`로 파셜(volatile) 10개 → 파이널 1개 순서 확인. `result.isFinal`로 구분. |

**중대 관심사(비차단):** `say` TTS 합성 음성의 전사 품질이 낮음 ("this is a test" → "TC Zo, Testu"). API 메커니즘은 완전히 검증됨. 실제 사람 음성/회의 오디오로 품질 재확인 필요.

---

## 환경 / 빌드 방법

- 스파이크는 임시 SwiftPM 패키지(3개 실행 타깃 verify1/2/3). `swift main.swift` 단일 파일 실행도 가능하나, 여러 파일·타깃 관리를 위해 패키지가 편했음.
- **Swift 6 strict concurrency 주의:** `swift-tools-version: 6.0` 기본(Swift 6 모드)에서는 `TranslationSession`·`AVAudioPCMBuffer` 등이 `Sendable`이 아니라 "sending risks data race" **에러**가 남. 프로덕션 패키지(`native_helper_mac/Package.swift`)는 `swift-tools-version: 5.9`(= Swift 5 언어 모드)라 이 문제들이 에러가 아니므로 그대로 컴파일된다. 스파이크는 타깃별 `swiftSettings: [.swiftLanguageMode(.v5)]`로 프로덕션과 동일 조건을 맞췄다.
  - 프로덕션이 새 타깃을 tools 6.0/Swift 6 모드로 만들 경우, `@MainActor` 격리와 명시적 `nonisolated(unsafe)`/`@unchecked Sendable` 래핑이 추가로 필요하다. → **새 타깃은 Swift 5 언어 모드 권장.**
- Speech.framework swiftinterface 위치(정확한 시그니처 확인용):
  `…/MacOSX.sdk/System/Library/Frameworks/Speech.framework/Versions/A/Modules/Speech.swiftmodule/arm64e-apple-macos.swiftinterface`

---

## 검증 1: 헤드리스 TranslationSession (최대 리스크) — PASS

### 결과 (실행 출력)
```
acquiring session...
session acquired in 0.081s
translate elapsed 3.435s      # 첫 호출 워밍업 포함
KO: 안녕하세요, 이것은 테스트입니다.
KO: 애니메이션 타이밍이 이상해 보입니다.
```

### 확정 사항
- **`NSApplication.shared` + `app.setActivationPolicy(.prohibited)` + `app.run()` 필요.** RunLoop 단독으로는 SwiftUI `.translationTask`가 세션을 발화하지 않는다(뷰가 렌더 사이클을 돌아야 함). `.prohibited`로 Dock 아이콘/포커스 탈취 없음.
- **숨김 윈도우 패턴 유효:** `NSWindow`(borderless, 1x1) + `NSHostingView(rootView: Color.clear.translationTask(config){…})`, `orderBack(nil)` + `alphaValue = 0`. 윈도우/호스팅뷰는 **강한 참조로 계속 살려둬야** 세션이 유효(뷰가 죽으면 세션도 무효).
- `translationTask` 클로저가 준 `TranslationSession`은 **클로저 밖으로 escape 가능** — `CheckedContinuation`으로 꺼내 async 함수에서 사용 OK.
- **`TranslationSession`·클로저는 `@MainActor` 격리.** 브리지 클래스를 `@MainActor`로 만들고 세션 사용도 메인 액터에서 하면 크로스-액터 sending 문제를 피한다.
- `session.translations(from: [TranslationSession.Request])` → `[TranslationSession.Response]`, `response.targetText`가 번역문. **배치 API라 매우 빠름**(로컬 NMT).
- EN→KO 언어팩은 검증 머신에 **이미 설치돼 있어** 다운로드 에러 없이 즉시 번역됨. (미설치 시 동작은 아래 "언어 에셋" 참고.)

### 확정 코드 (Task 3~5 기준)
```swift
import AppKit
import SwiftUI
import Translation

@available(macOS 15.0, *)
@MainActor
final class TranslatorBridge {
    private var window: NSWindow?
    private var continuation: CheckedContinuation<TranslationSession, Never>?

    func acquireSession(source: Locale.Language, target: Locale.Language) async -> TranslationSession {
        await withCheckedContinuation { (cont: CheckedContinuation<TranslationSession, Never>) in
            self.continuation = cont
            let config = TranslationSession.Configuration(source: source, target: target)
            let host = NSHostingView(rootView:
                Color.clear.translationTask(config) { session in
                    self.continuation?.resume(returning: session)
                    self.continuation = nil
                })
            let win = NSWindow(contentRect: .init(x: 0, y: 0, width: 1, height: 1),
                               styleMask: [.borderless], backing: .buffered, defer: false)
            win.contentView = host
            win.orderBack(nil)
            win.alphaValue = 0
            self.window = win   // 반드시 강한 참조 유지
        }
    }
}

// 진입점: NSApplication + run() 필수
let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
Task { @MainActor in
    let bridge = TranslatorBridge()
    let session = await bridge.acquireSession(source: .init(identifier: "en"),
                                              target: .init(identifier: "ko"))
    let responses = try await session.translations(from: [
        .init(sourceText: "Hello, this is a test."),
        .init(sourceText: "The animation timing looks off."),
    ])
    for r in responses { print("KO:", r.targetText) }
    exit(0)
}
app.run()
```

### 프로덕션 주의점
- `translate-batch` / `live` 서브커맨드 모두 `NSApplication.run()` 위에서 돌아야 한다. Python subprocess에서 stdin/stdout JSONL을 쓰려면 **stdin 읽기 루프를 별도 `Task`/백그라운드에서 돌리고 메인 스레드는 `app.run()`에 양보**하는 구조로 짠다.
- 재사용: 세션을 한 번 확보해 프로세스 수명 동안 재사용(윈도우 유지). 언어쌍이 고정(EN→KO)이므로 세션 1개면 충분.
- 언어팩 미설치 시 `translations(from:)`가 던지는 에러를 잡아 `status:error reason:missing_mt_asset` 방출. (프로그램적 MT 다운로드는 `TranslationSession.prepareTranslation()` 사용 가능 — 아래 참고. 스파이크에선 이미 설치돼 미검증.)

---

## 검증 2: SpeechTranscriber 파일 전사 + audioTimeRange — PASS

### 결과 (실행 출력)
```
supportedLocales count=30 contains en-US? true
installedLocales: ["ko-KR"]                 # ← en-US 미설치 상태였음
asset installation required — downloading...
asset download+install complete in 20.300s  # ← 프로그램적 다운로드 성공
audio format: 1 ch, 16000 Hz, Float32, length frames: 62459
analyzeSequence returned lastSampleTime=3.903625
RESULT isFinal=true text="Hello, TC Zo, Testu, but our transcription system."
file analysis wall time: 0.341s for 62459 frames   # ≈11x 실시간
total runs with audioTimeRange: 8
  run t0=0.000 t1=0.600  "Hello,"
  run t0=0.600 t1=1.320  " TC"
  run t0=1.320 t1=1.740  " Zo,"
  run t0=1.740 t1=2.280  " Testu,"
  run t0=2.280 t1=2.340  " but"
  run t0=2.340 t1=2.580  " our"
  run t0=2.580 t1=3.120  " transcription"
  run t0=3.120 t1=3.904  " system."
```

### 확정 API 시그니처 (swiftinterface 실측)
```swift
// 생성자
SpeechTranscriber(locale: Locale,
                  transcriptionOptions: Set<SpeechTranscriber.TranscriptionOption>,   // 예: [], [.etiquetteReplacements]
                  reportingOptions: Set<SpeechTranscriber.ReportingOption>,           // .volatileResults / .alternativeTranscriptions / .fastResults
                  attributeOptions: Set<SpeechTranscriber.ResultAttributeOption>)      // .audioTimeRange / .transcriptionConfidence

// 로케일 조회 (모두 async)
static var SpeechTranscriber.supportedLocales: [Locale] { get async }
static var SpeechTranscriber.installedLocales: [Locale] { get async }
// 비교: locale.identifier(.bcp47) == "en-US"

// 분석기 (actor)
SpeechAnalyzer(modules: [any SpeechModule])
func SpeechAnalyzer.analyzeSequence(from audioFile: AVAudioFile) async throws -> CMTime?   // 파일 전사 편의 API
func SpeechAnalyzer.finalizeAndFinishThroughEndOfInput() async throws                       // 결과 스트림 종료 유도

// 결과 스트림
var SpeechTranscriber.results: some Sendable & AsyncSequence<SpeechTranscriber.Result, any Error>

// SpeechTranscriber.Result 구조체
struct Result {
    let range: CMTimeRange                 // 이 결과 세그먼트의 시간 범위
    let resultsFinalizationTime: CMTime
    var text: AttributedString             // 전사 텍스트 (run별 속성 부착)
    let alternatives: [AttributedString]
}
// isFinal은 SpeechModuleResult 프로토콜 익스텐션의 계산 프로퍼티: result.isFinal -> Bool
```

### audioTimeRange 추출 (확정)
`result.text`(AttributedString)의 각 run에서 `run.audioTimeRange`(dynamicMember)로 **`CMTimeRange`** 를 얻는다(옵셔널). `.audioTimeRange` 속성은 `attributeOptions: [.audioTimeRange]`를 켜야 채워진다.
```swift
for run in result.text.runs {
    if let tr = run.audioTimeRange {                 // CMTimeRange (nil이면 이 run엔 타임레인지 없음)
        let seg = String(result.text[run.range].characters)
        let t0 = tr.start.seconds                     // Double 초
        let t1 = tr.end.seconds                       // == (tr.start + tr.duration).seconds
        // seg를 t0~t1 세그먼트로 방출 (JSONL final의 t0/t1)
    }
}
```
- `.audioTimeRange` 속성 스코프: `AttributeScopes.SpeechAttributes.TimeRangeAttribute` (값 타입 `CMTimeRange`). `import Speech`만으로 dynamicMember 접근 됨(별도 스코프 지정 불필요, 컴파일 확인).
- 편의: `AttributedString.rangeOfAudioTimeRangeAttributes(intersecting: CMTimeRange)`도 존재.

### 확정 코드 (파일 전사)
```swift
// 주의: 아래 심볼은 macOS 26+ 전용 — 배포 타깃이 낮은 바이너리에서는 @available(macOS 26.0, *) 가드 필수
let transcriber = SpeechTranscriber(locale: Locale(identifier: "en-US"),
                                    transcriptionOptions: [], reportingOptions: [],
                                    attributeOptions: [.audioTimeRange])
// 자산 보장 (아래 "언어 에셋" 참고)
let analyzer = SpeechAnalyzer(modules: [transcriber])
let file = try AVAudioFile(forReading: url)

let collector = Task {
    for try await result in transcriber.results {
        // result.isFinal, result.text, run.audioTimeRange 처리
    }
}
_ = try await analyzer.analyzeSequence(from: file)
try await analyzer.finalizeAndFinishThroughEndOfInput()   // ← 없으면 results가 끝나지 않음
await collector.value
```
**주의:** `results` 순회 Task를 `analyzeSequence` 호출 **전에** 시작해 둘 것. `finalizeAndFinishThroughEndOfInput()`를 호출해야 `results` AsyncSequence가 종료된다.

---

## 검증 3: SpeechTranscriber 스트리밍 (volatile 파셜) — PASS

### 결과 (실행 출력)
```
analyzer format: 1 ch, 16000 Hz, Int16
[VOLATILE #1] "Hello"
[VOLATILE #2] "Hello,"
[VOLATILE #3] "Hello, T"
...
[VOLATILE #10] "Hello, TC test but transcription system."
[FINAL   #1] range=0.00-3.90  "Hello, TC Zo, Testu, but our transcription system."
volatile=10 final=1
```
→ **파셜(volatile) 누적 갱신 후 파이널 1개**. 순서·구분 확인.

### 확정 API 시그니처
```swift
// 스트리밍 입력 요소
struct AnalyzerInput : @unchecked Sendable {
    init(buffer: AVAudioPCMBuffer)
    init(buffer: AVAudioPCMBuffer, bufferStartTime: CMTime?)
}

// 입력 시퀀스 연결 (start 후 스트림에 yield하면 처리됨)
func SpeechAnalyzer.start<S: Sendable & AsyncSequence>(inputSequence: S) async throws
    where S.Element == AnalyzerInput

// 입력 포맷: 버퍼를 이 포맷으로 변환해 넣어야 함
static func SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [any SpeechModule]) async -> AVAudioFormat?
// 검증 머신 실측: 1ch, 16000 Hz, Int16
```

### 확정 사항
- `reportingOptions: [.volatileResults]` 켜면 파셜이 온다. **파셜은 `result.isFinal == false`, 파이널은 `true`.** 파셜은 텍스트가 계속 자라며 갱신되고, 확정되면 같은 구간에 대해 `isFinal == true` 결과 1개가 온다.
- 입력은 `AsyncStream<AnalyzerInput>.makeStream()` → `analyzer.start(inputSequence: stream)` → `continuation.yield(AnalyzerInput(buffer:))` → 끝나면 `continuation.finish()` + `analyzer.finalizeAndFinishThroughEndOfInput()`.
- **입력 PCM은 `bestAvailableAudioFormat`(16kHz Int16)로 변환 필요.** 소스가 16kHz Float32여도 Int16으로 `AVAudioConverter` 변환해야 함. (라이브 회의 캡처가 16kHz mono PCM이면 Int16 변환만 하면 됨.)
- 파셜 스로틀(설계의 ~500ms)은 애플리케이션 레벨에서 구현: volatile 결과를 시간/글자수 기준으로 스로틀해 `TranslationSession` 배치로 번역 후 `partial` 방출.

### 확정 코드 (스트리밍 핵심)
```swift
// 주의: 아래 심볼은 macOS 26+ 전용 — 배포 타깃이 낮은 바이너리에서는 @available(macOS 26.0, *) 가드 필수
let transcriber = SpeechTranscriber(locale: Locale(identifier: "en-US"),
    transcriptionOptions: [], reportingOptions: [.volatileResults],
    attributeOptions: [.audioTimeRange])
guard let fmt = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else { … }
let analyzer = SpeechAnalyzer(modules: [transcriber])
let (stream, cont) = AsyncStream<AnalyzerInput>.makeStream()

let collector = Task {
    for try await r in transcriber.results {
        if r.isFinal { /* final: r.range.start/end.seconds, r.text */ }
        else         { /* volatile partial: r.text */ }
    }
}
try await analyzer.start(inputSequence: stream)
// PCM 청크(예: 0.1s)마다: AVAudioConverter로 fmt(16k Int16) 변환 후
cont.yield(AnalyzerInput(buffer: convertedBuffer))
// 입력 종료
cont.finish()
try await analyzer.finalizeAndFinishThroughEndOfInput()
await collector.value
```

---

## 언어 에셋 (다운로드 동작) — 중요

### STT 모델 (Speech)
- **프로그램적 다운로드 성공, 시스템 설정 UI 불필요.** 검증 머신은 en-US 미설치(installedLocales=["ko-KR"])였으나 다음으로 다운로드+설치됨(~20s):
```swift
// 미설치 시 request가 non-nil로 반환됨. 이미 설치면 nil.
if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
    try await request.downloadAndInstall()   // ProgressReporting 준수 → request.progress로 진행률
}
// 상태 조회: await AssetInventory.status(forModules: [transcriber]) -> AssetInventory.Status
```
- `AssetInstallationRequest`는 `ProgressReporting` + `Sendable`. 진행률 노출 가능.
- 에러 타입: (미검증 — 오디오 포맷 에러 코드이며 에셋 누락 에러 타입으로 확인된 것 아님) `SFSpeechError`(코드 예: `.unexpectedAudioFormat`, `.incompatibleAudioFormats`). 스파이크에선 다운로드 에러 미발생.

### MT 팩 (Translation)
- 검증 머신에 EN→KO **이미 설치**돼 있어 다운로드 경로 미검증. 프로덕션에서 미설치 대비:
  - `TranslationSession.prepareTranslation()`으로 사전 다운로드 트리거 가능(문서상). 미검증.
  - 미설치 시 `translations(from:)`가 throw → 에러 잡아 `status:error reason:missing_mt_asset` 방출. **에러 타입은 스파이크에서 실측 못 함**(팩이 이미 있어서). Task 3 구현 시 미설치 머신에서 실제 던지는 에러 타입 확인 필요. → **미해결 관심사.**
  - > ⚠️ **Task 3 인계**: MT 언어팩 미설치 시 `translations(from:)`이 던지는 에러 타입은 미검증. Task 3 구현자는 이 노트가 그 케이스를 답한다고 가정하지 말 것 — 미설치 상태를 만들어 실측하거나, 임의 에러를 `status:error reason:missing_mt_asset`으로 매핑하는 방어적 처리를 할 것.
  - 사용자 지시: 시스템 설정 UI를 코드로 열지 말 것. 미설치이고 프로그램적 설치 불가하면 `status:error`로 보고.

---

## 타이밍 관측치 (검증 머신)

| 항목 | 값 |
|---|---|
| TranslationSession 확보 | ~0.08s |
| EN→KO 배치 번역(2문장, 첫 호출 워밍업 포함) | ~3.4s (이후 호출은 훨씬 빠를 것으로 예상) |
| 파일 전사(3.9s 오디오) | 벽시계 0.34s ≈ **11x 실시간** |
| STT en-US 모델 다운로드+설치 | ~20s |

## 미해결 / 관심사

1. **전사 품질(비차단):** `say` TTS 합성 음성에서 "this is a test" → "TC Zo, Testu"로 오인식. API 메커니즘은 완전 검증. **실제 사람 음성/회의 오디오로 정확도 재확인 필요.** 합성음 특유의 아티팩트일 가능성 높음.
2. **MT 팩 미설치 에러 타입 미실측:** EN→KO가 이미 설치돼 있어 `translations(from:)` 실패 경로를 못 봄. Task 3 구현 시 미설치 환경(또는 팩 삭제)에서 실제 에러 타입 확정 필요.
3. **Swift 언어 모드:** 새 프로덕션 타깃은 **Swift 5 언어 모드** 권장(Sendable strict-concurrency 마찰 회피). Swift 6 모드로 갈 경우 `@MainActor` 격리와 `@unchecked Sendable` 래핑 추가 필요.
4. **첫 번역 워밍업:** 첫 `translations` 호출에 수 초 지연. 프로세스 시작 시 더미 문장으로 워밍업 권장.
```
