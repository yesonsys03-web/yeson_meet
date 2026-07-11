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

---

## 실기 종단 검증 (Task 11)

- 날짜: 2026-07-11 / 커밋: `6a8d92b`
- 머신: Apple Silicon(arm64), macOS 26.4.1 (25E253)
- 사용 바이너리: 릴리스 빌드 `apps/server_desktop/.../apple-translate-aarch64-apple-darwin/apple-live-translate` (Mach-O arm64)
- **달성 레벨(중요):** 자막메이커는 **실제 서버 REST API 종단**(로컬 Postgres + `uvicorn`,
  `POST /api/v1/video-jobs/upload`, `whisper_model=apple`)으로, 라이브는 **컴포넌트 종단**
  (실 바이너리 `live` + 실제 `AppleLiveTranslateProvider`·`AudioLiveSession`·
  `AISequenceNormalizer`·`is_permanent_provider_error`)으로 검증했다. 라이브의 WS
  sidecar/디바이스 인증 전송 계층은 구동하지 않았다(브리프의 fallback 허용 범위) —
  단, provider→orchestration 경계의 실제 프로덕션 클래스는 전부 실물로 돌렸다.

### 결론 요약

| 항목 | 결과 |
|---|---|
| 자막메이커 전사(Apple STT) | ✅ 10.3s 영상 → **~0.40s** (≈26x 실시간), 세그먼트 정확 |
| 자막메이커 번역(Apple MT) | ✅ 2 세그먼트 배치 **~2.2s** (서브프로세스당 NSApplication+세션 워밍업 포함) |
| 자막메이커 굽기(burn) | ✅ **~0.33s** (10.3s/320x240, 번들 ffmpeg `ffmpeg-aarch64-apple-darwin` = libass 포함, `YESON_FFMPEG_BIN` 지정). 시스템 Homebrew ffmpeg 8.1은 libass 미포함이라 최초 시도는 실패했었음(환경 제약, 제품 버그 아님). |
| 라이브 메커니즘 | ✅ volatile 파셜 + 파이널(seq·EN·KO·t0/t1)이 실 바이너리·실 프로바이더로 정상 흐름 |
| 라이브 콜드스타트 | ~5.0s (프로세스 기동→첫 자막; STT 모델+TranslationSession 워밍업, 스파이크 ~3.4s 관측과 일치) |
| 라이브 phrase-end→final (정상상태) | P50 **~2.1–2.8s** (표본 N=7 발화/2런 — 소표본, 지표성) — Gemini 실측 P50 1419.8ms(8발화)보다 **느림** |
| 엣지(a) 영구에러 | ✅ `unsupported_os`→`AppleProviderUnavailable`→영구 분류→운영자 provider_error 1회→3s창 스폰 1회(재접속 스팸 없음) |
| 엣지(b) kill -9 재접속 | ✅ 재접속(provider_segment 1→2) + normalizer가 seq 단조 유지(1,2,3→4,5,6) |

### 1. 자막메이커 (REST API 종단)

- 절차: 스크래치 Postgres DB + `create_schema()`/`seed()`(운영자 계정) →
  `python -m apps.server.main`(port 8787) → 10.3s 테스트 mp4(`say`+ffmpeg 생성)를
  `POST /video-jobs/upload`로 업로드(`whisper_model=apple`). 번역 엔진 Apple은
  API의 `translate_provider` 정규식(`^(gemini|claude|codex|agy|opencode)$`)이
  "apple"을 받지 않아 (수정됨: `7e7973a` — 패턴에 apple 허용) 당시에는 서버 env
  `YESON_VIDEO_TRANSLATE_PROVIDER=apple`로 선택
  (파이프라인 `create_translator(provider=None)`가 env 폴백 → `AppleTranslator`).
  `GET /video-jobs/translate-engines`는 `apple: available=true` 노출 확인.
- 단계별 실측(고빈도 폴링, `rebuild`로 재실행한 **웜** 값):

  | 단계 | 상태 | 소요 |
  |---|---|---:|
  | 추출(extracting: preview+audio.wav) | | ~0.06s |
  | 전사(transcribing, Apple STT) | 0→100% | **~0.40s** |
  | 번역(translating, Apple MT 2세그먼트) | | **~2.20s** |
  | **합계(→review)** | | **~2.7s** |
  | 굽기(burning→done, 번들 ffmpeg libx264) | 0→100% | **~0.33s** |

  - 최초 업로드(콜드, 바이너리·모델 웜업 포함)는 review까지 **~12.0s**. 재실행(웜) ~2.7s.
  - 산출 세그먼트 예: `seq=1 0–3780ms "Hello, everybody. Welcome to today's meeting." / 안녕하세요, 여러분. 오늘 회의에 오신 것을 환영합니다.`
- **whisper/Gemini 비교는 브리프 지침에 따라 생략**: `STORAGE_ROOT/whisper_models/` 아래
  다운로드된 whisper 모델이 없어(수 GB 다운로드 금지) 전사 비교 불가 → Apple-only 기록.
  (`GEMINI_API_KEY`는 env에 존재했으나 비교 조건은 whisper 모델 존재를 함께 요구.)
- 굽기: 최초 시도는 시스템 Homebrew ffmpeg 8.1로 실패(`code=234, "Error parsing filterchain
  'subtitles=...'"` — **libass 미탑재**; 필터 문자열 자체는 유효). 리뷰 반영으로 디스크의
  **번들 ffmpeg**(`apps/server_desktop/src-tauri/binaries/ffmpeg-aarch64-apple-darwin/ffmpeg`,
  8.1.2-tessus, `subtitles`/`ass` 필터 확인)를 `YESON_FFMPEG_BIN`으로 지정해 재측정:
  `POST /burn` → burning→done **~0.33s**, burned.mp4(h264/aac, 10.29s) 정상 생성. 즉 배포
  구성(번들 ffmpeg)에서는 굽기 정상 동작 — 최초 실패는 환경 제약이었음.

### 2. 라이브 종단 (컴포넌트 레벨)

- 절차: 실 바이너리 `live`에 16kHz mono Int16 PCM을 **실시간 페이스**(100ms 청크)로
  주입, stdout JSONL을 벽시계 타임스탬프와 함께 파싱. 오디오는 `say`+ffmpeg 합성
  (영어 다문장, 800~900ms 무음 구분). phrase-end 기준은 각 final의 `t1`(오디오 시각),
  지연 = `수신벽시계 − (feed_start + t1)`.
- **콜드스타트**: 프로세스 기동 후 첫 파셜까지 **~5.0s**(STT 모델 로드 + TranslationSession
  워밍업). 실제 회의에선 세션 시작 시 1회성. 스파이크의 "첫 번역 ~3.4s 워밍업" 관측과 정합.
- **정상상태 phrase-end→final**: 프로세스를 선(先)워밍한 뒤 측정 시 **P50 ~2.1–2.8s**,
  min ~0.95s(입력 EOF로 강제 finalize된 마지막 발화), max ~3.3s. 즉 완결(구두점·번역·
  용어보정 포함) **final 지연이 Gemini의 phrase-end→first-subtitle P50 1419.8ms보다 크다**.
  - **표본 수: 총 N=7 발화, 2개 런** — 런1(9문장 클립, t1≥9s만 측정) N=5
    [2765, 3304, 1400, 2881, 948ms], 런2(10s 선워밍+8문장) N=2 [2004, 1230ms].
    Gemini 기준치(1419.8ms)도 8발화 1런이었으나, **N<10의 소표본**이므로 위 P50/min/max는
    통계적 확정치가 아니라 지표(indicative)다. 실회의 오디오 + 더 긴 세션으로 재측정 필요.
  - Apple은 발화 도중 volatile 파셜을 계속 방출하므로 "무언가 보이기까지"는 더 빠르나,
    `isFinal` 확정에 후행 오디오(무음) 확인 창이 필요해 final이 ~2–3s 지연된다. 마지막
    발화가 EOF 강제 finalize로 <1s에 나온 점이 이를 뒷받침(버퍼링 아님).
- **⚠️ 신뢰도 단서:** 측정 오디오가 `say` 합성음이라 STT 인식·세그먼테이션 품질이 낮고
  (스파이크가 이미 경고한 리스크) 이것이 finalization 타이밍을 왜곡한다. **실제 회의
  오디오로 재측정해야 확정 수치**가 된다. 위 수치는 지표(indicative)로 해석할 것.
- **트레이드오프:** Apple = 네트워크/과금 0·오프라인·배치 처리량 압도(파일 전사 26x 실시간,
  번역 배치 초 단위). 반면 라이브 **final 지연은 Gemini보다 큼**. → 최저 라이브 지연이
  최우선이면 Gemini, 비용/오프라인/자막메이커 처리량이 우선이면 Apple.

### 3. 엣지 케이스

**(a) 영구 에러(언어팩/OS 미지원 계열) — provider_error + 백오프.** (지침에 따라 시스템
언어팩은 건드리지 않고) `YESON_APPLE_TRANSLATE_BIN`을 `{"type":"status","state":"error",
"reason":"unsupported_os"}` 방출 후 exit 3 하는 가짜 스크립트로 지정하고 라이브 세션 구동:

```
is_permanent_provider_error(unsupported_os) = True   (msg: "apple provider unavailable: unsupported_os")
AI live session provider rejected request (permanent)      ← 로그
fake-binary spawns in 3s window = 1   (재접속 스팸 없음; PERMANENT_ERROR_BACKOFF_SECONDS=300)
on_permanent_error invocations = 1    ← sidecar가 _publish_provider_error(운영자 provider_error)로 연결
utterances emitted = 0
```

→ 영구 에러로 분류되어 운영자에게 provider_error 1회 표출 + 5분 백오프, 3초 관측창에
스폰 1회뿐이라 **짧은 백오프 재접속 스팸 없음** 확인. PASS.

**(b) 라이브 중 `kill -9` → 자동 재접속 + seq 연속성.** 실 바이너리 세션 구동 중
세그먼트1이 파이널 몇 개를 낸 뒤(t≈9s) `pkill -9 -f "apple-live-translate live"`:

```
RuntimeError: apple live exited rc=-9   → AudioLiveSession 재접속 루프
provider_segments observed = [1, 2]                       (재접속 발생)
finals (norm_seq, provider_segment): (1,1)(2,1)(3,1)(4,2)(5,2)(6,2)
normalized final seqs strictly-increasing = True
```

→ 프로바이더는 재접속(segment2)에서 내부 seq를 1,2,3으로 다시 시작하지만
`AISequenceNormalizer`가 직전 세그먼트 마지막 seq(3)만큼 오프셋해 4,5,6으로 재배치 →
**재접속 경계를 넘어 자막 seq가 단조 증가 유지**. PASS.

### 4. 검증 방법 메모(재현용)

- 서버 기동: `DATABASE_URL=postgresql+asyncpg://…/yeson_meet_e2e JWT_SECRET=… STORAGE_ROOT=<scratch>
  YESON_AI_PROVIDER=apple_live_translate YESON_APPLE_TRANSLATE_BIN=<릴리스 바이너리>
  YESON_VIDEO_TRANSLATE_PROVIDER=apple PORT=8787 uv run --project apps/server python -m apps.server.main`
  (테이블은 `apps.server.db.seed.create_schema()`, 운영자 계정은 `seed()`로 사전 생성).
  굽기 실측 시에는 `YESON_FFMPEG_BIN=<repo>/apps/server_desktop/src-tauri/binaries/ffmpeg-aarch64-apple-darwin/ffmpeg`
  추가(시스템 Homebrew ffmpeg는 libass 미포함).
- 오디오 생성: `say`(+`[[slnc N]]` 무음)→`ffmpeg -ar 16000 -ac 1`로 wav(전사) / `-f s16le` raw PCM(라이브).
- 콜드/웜 구분: 각 서브프로세스는 매 호출 새로 뜨므로 STT/MT 워밍업 비용을 매번 지불한다
  → 프로세스 재사용/사전 워밍업(더미 입력)으로 완화 권장(§미해결 4와 동일 결론, 라이브에도 적용).

## 두-모델 시스템 (성능 후속, 2026-07-11)

macOS 26.4의 Translation 프레임워크는 **두 개의 NMT 모델**을 노출한다:
`TranslationSession.Strategy.highFidelity`(기본, 고품질)와 `.lowLatency`(저지연). 자막
용도로는 lowLatency 품질이 충분하면서 훨씬 빠르다.

### API 계층 (swiftinterface 실측)

- `TranslationSession(installedSource:target:)` — **macOS 26.0+**. SwiftUI 숨김 윈도우
  브리지 없이 세션을 직결 생성. 26.0 미만은 여전히 `.translationTask` 브리지 필요.
- `TranslationSession(installedSource:target:preferredStrategy:)` — **macOS 26.4+**. 전략 지정.
- `LanguageAvailability(preferredStrategy:).status(from:to:)` — **macOS 26.4+**. 전략별
  설치 상태(`.installed`/`.supported`/`.unsupported`)를 개별 보고.
- `TranslationSession.Strategy` / `Configuration.preferredStrategy` — **macOS 26.4+**.

### 실측 성능 (검증 머신, M2 Pro)

- 453큐/42KB 전체: highFidelity **113 chars/s** vs lowLatency **1,925 chars/s = 17배**.
- 50큐 배치(scratch 픽스처, 실 바이너리): `YESON_APPLE_MT_STRATEGY=low` **3.06s** vs
  `high` **41.94s** (약 13.7배). low가 기본이며 품질은 자막용으로 충분.

### lowLatency 팩은 별도 UI-프롬프트 다운로드

lowLatency EN→KO 언어팩은 highFidelity와 **별개 에셋**으로, `prepareTranslation()`을
**보이는 창의 `.translationTask` 안**에서 호출해 시스템 다운로드 확인창을 유도해야 한다
(비-UI 컨텍스트는 `notInstalled` throw). 이를 위해 `apple-live-translate prepare-translation`
서브커맨드를 추가했다 — 유일하게 regular 활성화 정책으로 작은 창을 띄운다. server_desktop의
"고속 번역 모델 설치" 버튼(`install_fast_translation` command)이 이를 실행한다.

### 세션 팩토리 + 폴백

`SessionFactory.makeTranslationSession(...)`이 batch/live 공용 진입점. 26.4+에서
`LanguageAvailability(preferredStrategy:)`로 요청 전략 설치 여부를 확인해:
low 요청·설치 → lowLatency, low 요청·미설치 → stderr 경고 1회 후 highFidelity 폴백,
아무것도 미설치 → `AppleMTMissingAsset`(→ `missing_mt_asset` 계약). 26.0..<26.4는 직결
init(highFidelity), 15..<26.0은 기존 브리지.

### 환경변수

- `YESON_APPLE_MT_STRATEGY` = `low`(기본) | `high` — 번역 전략 스위치.
- `YESON_BURN_PRESET` = `veryfast`(기본) | `superfast` | `ultrafast` — libx264 굽기 프리셋.

## 라이브 문장 분절 + 듀얼-세션 전략 (2026-07-11)

SpeechTranscriber 자체 발화 확정(`isFinal`)에만 의존하면 연속 회의 발화에서 파이널이
몰렸다가(3s에 6개) 한 볼래틸 발화가 자라는 동안 15~25s씩 비는 문제가 있다. 검증된
Gemini 경로(`gemini_live_translate.py`의 `TranscriptAssembler`) 의미론을 볼래틸 EN 스냅샷
스트림에 이식해 **문장 단위 강제 파이널**을 만든다.

### SentenceSegmenter (`Sources/AppleTranslateKit/SentenceSegmenter.swift`)

순수 struct(시계는 `now` 주입). Gemini는 프래그먼트를 버퍼에 누적하지만 SpeechTranscriber는
현재 발화의 **전체 텍스트 스냅샷**을 갱신하므로, 이미 확정 방출한 접두사(`consumedPrefix`)를
추적하고 그 뒤 미소비 suffix에만 판단을 적용한다. 컷 규칙(우선순위 순):

1. **문장 경계**(`. ` `? ` `! ` `…`) — 소수점 가드(숫자.숫자, 예 "1.5"는 경계 아님, Gemini
   규칙 미러). 완결 판정: 구두점 뒤 공백이 오거나 suffix 끝+성장정지 grace(0.8s). `min_final`
   (12자) 미만이면 컷하지 않고 다음 경계까지 병합(마지막 완결 경계에서 컷 → 짧은 앞 문장 병합).
2. **길이 백스톱**(>120자) → 마지막 단어 경계에서 컷.
3. **나이 백스톱**(미소비 텍스트 >12s) → 통째로 컷.
4. **idle flush**(성장 정지 >2s) → 통째로 컷(min_final 무시).

`isForced` 플래그로 강제 컷 vs 진짜 `isFinal` flush 구분. 실제 transcriber `isFinal`은
잔여 suffix를 비강제 파이널로 flush 후 내부 reset(새 발화). 모든 임계값은 생성자 파라미터
(테스트 용이성). 단위 테스트 11종(`Tests/…/SentenceSegmenterTests.swift`) 전부 GREEN.

### 듀얼-세션 전략 (runLive)

파셜/파이널 번역을 각각 별도 전략 세션으로 처리 가능. **사용자 결정(품질 평가 우선):
라이브 기본은 파셜·파이널 모두 highFidelity**(화면 지속 문장 품질 > 파셜 스냅성). 두 전략이
같으면 세션 하나만 만든다(동일 세션 2개 스폰 방지). 26.4 미만은 오늘과 동일 단일 세션.

- `YESON_APPLE_LIVE_FINAL_STRATEGY` = `high`(기본) | `low`
- `YESON_APPLE_LIVE_PARTIAL_STRATEGY` = `high`(기본) | `low` — `low`로 두면 빠른 러프 파셜 복원
- 배치(`translate-batch`)는 그대로 `YESON_APPLE_MT_STRATEGY` 기본 `low`.

> ⚠️ **트레이드오프:** highFidelity 파셜은 스로틀 틱마다 ~0.4~0.9s를 더한다(highFidelity ≈
> 113 chars/s vs lowLatency ≈ 1,925 chars/s). 실사용에서 파셜이 느리면 파셜 노브를 `low`로
> 뒤집는다.

### 파셜은 미소비 suffix만 노출

파셜 라인은 분절기가 남긴 **현재 문장 suffix만** 번역·방출한다(이미 확정한 문장이 파셜에
재노출되지 않게). 강제 컷 시 `LiveEmitPolicy.onFinal`이 스로틀을 리셋해 줄어든 파셜이 즉시
표시된다. Python(`apple_live_translate.py`)은 변경 불필요: seq는 세그먼트별 단조(정책 제공),
partial/final 이벤트 shape 불변.

### 실기 스모크 (say 합성음)

- 다문장 클립: 문장 경계 컷으로 파이널 다수(문장 단위), seq 단조(1,2,3), 파셜은 suffix만, exit 0.
- 30.8s 구두점-없는 런온(실시간 페이스): 백스톱이 발화 도중 컷 → 파이널 3개(giant final 아님),
  seq 단조, t0/t1 단조. (say 합성음 특성상 STT 텍스트 품질은 낮으나 분절 메커니즘은 정상 — 스파이크
  §미해결 1과 동일, 실회의 오디오 재확인 권장.)

### 굽기(자막 번인) 실측 — GPU 인코더 기각

60s·1080p60 기준 프리셋별: veryfast **10.8s/8.6MB**, superfast **6.8s/18.6MB**,
ultrafast **4.8s/35.3MB**(빠를수록 파일 큼·품질↓). VideoToolbox(GPU) 굽기는 실측
**3.3배** 가속에 그쳐 x264 프리셋의 **6~7.5배**보다 느려 GPU 번인은 기각. 대신 CPU
libx264 프리셋 opt-in으로 급할 때 속도를 확보한다.

## 라이브 프로바이더 판정 (2026-07-11 실회의 평가)

**(a) 실회의 2회 평가 결과** — SpeechTranscriber는 volatile(비확정) 텍스트를 소급 수정하는
특성이 있어, 자막 리듬이 불규칙하게 흔들림(이미 표시된 파셜이 뒤늦게 바뀜). 번역 품질도
`lowLatency`·`highFidelity` 두 전략 모두 `gemini_live_translate` 대비 열세로 확인.

**(b) 결정** — `apple_live_translate`는 **실험적(experimental) / 오프라인 백업**으로만
유지한다. 회의 기본 프로바이더는 계속 `gemini_live_translate`. UI에도 실험적 표기와 경고
툴팁을 추가함(`ServerConfigPanel.tsx`).

**(c) 향후 후보** — 하이브리드 구성: 전사는 Apple 온디바이스(SpeechTranscriber) + 번역은
Gemini Flash를 문장 단위로 호출. 이번 스파이크에서 만든 문장분절기(`SentenceSegmenter`)를
그대로 재사용할 수 있음. 단, STT의 소급 수정(volatile→final 재작성) 문제는 이 구성에서도
별도로 해결해야 함(분절기가 소급 수정을 얼마나 흡수하는지 별도 검증 필요).

**(d) 자막메이커는 반대 결론** — 오프라인 배치(자막메이커) 용도로는 Apple이 확실한 승자.
전사 54배속(실측), 번역은 고속(22초)·고품질 선택 가능한 피커 제공(기본값은 고품질). 실시간
경로와 배치 경로의 트레이드오프가 다르므로 프로바이더 선택은 용도별로 분리 유지한다.
