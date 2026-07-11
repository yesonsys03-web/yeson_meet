import Foundation
import Translation

/// 번역 세션 확보 전략 선택 — env `YESON_APPLE_MT_STRATEGY`.
///
/// macOS 26.4가 도입한 두 모델(`.lowLatency` ≈ 1,925 chars/s vs `.highFidelity`
/// ≈ 113 chars/s, 실측 17배)을 스위칭한다. 자막 용도로는 lowLatency 품질이 충분해
/// 기본값을 low로 둔다. "high"만 명시적으로 highFidelity를 고른다(그 외/미설정은 low).
public enum AppleMTStrategy: Equatable {
    case low
    case high

    public static func fromEnv(
        _ env: [String: String] = ProcessInfo.processInfo.environment
    ) -> AppleMTStrategy {
        // 배치(translate-batch) 기본은 low — YESON_APPLE_MT_STRATEGY.
        from(env["YESON_APPLE_MT_STRATEGY"], defaultTo: .low)
    }

    /// 임의 env 값(문자열) → 전략. "high"/"low" 명시, 그 외/미설정은 `defaultTo`.
    /// 라이브 경로는 기본 high(YESON_APPLE_LIVE_*_STRATEGY)로, 배치는 기본 low로 쓴다.
    public static func from(_ raw: String?, defaultTo fallback: AppleMTStrategy) -> AppleMTStrategy {
        switch raw?.lowercased() {
        case "high": return .high
        case "low": return .low
        default: return fallback
        }
    }
}

/// 번역 에셋(EN→KO 언어팩)이 하나도 설치돼 있지 않을 때 팩토리가 던지는 에러.
/// 호출부(batch/live)는 이를 잡아 `missing_mt_asset` 계약으로 표면화한다.
/// `\(error)`가 "missing_mt_asset: ..." 를 그대로 내도록 CustomStringConvertible 채택.
public struct AppleMTMissingAsset: Error, CustomStringConvertible {
    public let detail: String
    public init(_ detail: String) { self.detail = detail }
    public var description: String { "missing_mt_asset: \(detail)" }
}

/// BatchTranslate/LiveTranslate 공용 세션 팩토리.
///
/// - macOS 26.4+: `LanguageAvailability(preferredStrategy:)`로 요청 전략의 설치 여부를
///   확인해 `TranslationSession(installedSource:target:preferredStrategy:)`를 쓴다.
///   low 요청인데 low 미설치면 stderr 경고 1회 후 highFidelity가 설치돼 있으면 그쪽으로
///   폴백한다. 아무것도 설치돼 있지 않으면 `AppleMTMissingAsset`을 던진다.
/// - macOS 26.0..<26.4: 전략 API가 없으므로 `TranslationSession(installedSource:target:)`
///   (highFidelity). 미설치면 이후 translations 호출이 던진다(오늘과 동일 계약).
/// - macOS 15..<26.0: installedSource 직결 init이 없어 기존 TranslatorBridge 숨김 윈도우
///   경로를 그대로 쓴다.
///
/// 두 호출부가 모두 @MainActor이고 TranslatorBridge도 @MainActor라 팩토리도 @MainActor로
/// 둔다 — 직결 init 세션도 메인 액터에서 안전하게 생성/사용된다.
@available(macOS 15.0, *)
@MainActor
public func makeTranslationSession(
    source: Locale.Language,
    target: Locale.Language,
    strategy: AppleMTStrategy = AppleMTStrategy.fromEnv()
) async throws -> TranslationSession {
    if #available(macOS 26.4, *) {
        switch strategy {
        case .low:
            let lowStatus = await LanguageAvailability(preferredStrategy: .lowLatency)
                .status(from: source, to: target)
            if lowStatus == .installed {
                return TranslationSession(
                    installedSource: source, target: target, preferredStrategy: .lowLatency)
            }
            // low 미설치 → 경고 1회 후 highFidelity 폴백 시도.
            FileHandle.standardError.write(
                ("apple-translate: lowLatency EN→KO 미설치 — highFidelity로 폴백 "
                 + "(고속 번역 모델은 prepare-translation으로 1회 설치 가능)\n")
                    .data(using: .utf8)!)
            let highStatus = await LanguageAvailability(preferredStrategy: .highFidelity)
                .status(from: source, to: target)
            if highStatus == .installed {
                // 명시적 .highFidelity가 plain init의 암묵 기본값 의존보다 결정적이라 의도적으로 선택 (리뷰 조율 완료)
                return TranslationSession(
                    installedSource: source, target: target, preferredStrategy: .highFidelity)
            }
            throw AppleMTMissingAsset("EN→KO lowLatency/highFidelity 모두 미설치")
        case .high:
            let highStatus = await LanguageAvailability(preferredStrategy: .highFidelity)
                .status(from: source, to: target)
            if highStatus == .installed {
                return TranslationSession(
                    installedSource: source, target: target, preferredStrategy: .highFidelity)
            }
            throw AppleMTMissingAsset("EN→KO highFidelity 미설치")
        }
    } else if #available(macOS 26.0, *) {
        // 전략 API 없음 — 직결 init(highFidelity)만. 미설치는 이후 translations가 던진다.
        return TranslationSession(installedSource: source, target: target)
    } else {
        // macOS 15..<26.0 — installedSource init 미존재, 숨김 윈도우 브리지 사용.
        let bridge = TranslatorBridge()
        return await bridge.acquireSession(source: source, target: target)
    }
}
