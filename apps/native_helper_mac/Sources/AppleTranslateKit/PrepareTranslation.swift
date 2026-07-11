import AppKit
import SwiftUI
import Translation

/// `prepare-translation` 서브커맨드 진입점.
///
/// 저지연(lowLatency) EN→KO 언어팩은 **UI 프롬프트 1회 다운로드**가 필요하다 —
/// `prepareTranslation()`은 보이는 창의 `.translationTask` 안에서만 시스템 확인창을
/// 띄울 수 있고, 비-UI 컨텍스트에서는 notInstalled를 던진다(scratch/download.swift에서
/// 사용자 검증됨). 따라서 이 서브커맨드만 예외적으로 regular 활성화 정책으로 작은 창을
/// 띄워 포커스를 잡는다(다른 서브커맨드는 prohibited/headless).
///
/// 성공 시 stdout에 `status ready` JSONL + exit(0), 실패 시 `status error` + nonzero.
/// app.run()이 블로킹하므로 종료는 View의 translationTask 클로저 안에서 exit()로 한다.
@available(macOS 15.0, *)
public func runPrepareTranslation() {
    let app = NSApplication.shared
    app.setActivationPolicy(.regular)
    let win = NSWindow(contentRect: .init(x: 0, y: 0, width: 480, height: 140),
                       styleMask: [.titled], backing: .buffered, defer: false)
    win.title = "고속 번역 모델 설치"
    win.contentView = NSHostingView(rootView: PrepareTranslationView())
    win.center()
    win.makeKeyAndOrderFront(nil)
    app.activate(ignoringOtherApps: true)
    app.run()
}

@available(macOS 15.0, *)
private struct PrepareTranslationView: View {
    // 26.4+에서는 lowLatency 전략을 명시해 저지연 팩을 받고, 그 이전에는 전략 없이
    // 기본(highFidelity) 팩을 준비한다.
    @State private var config: TranslationSession.Configuration? = {
        if #available(macOS 26.4, *) {
            var c = TranslationSession.Configuration(
                source: .init(identifier: "en"), target: .init(identifier: "ko"))
            c.preferredStrategy = .lowLatency
            return c
        }
        return TranslationSession.Configuration(
            source: .init(identifier: "en"), target: .init(identifier: "ko"))
    }()

    var body: some View {
        Text("저지연 번역 모델 다운로드 중… 확인창이 뜨면 승인해 주세요")
            .padding(40)
            .translationTask(config) { session in
                do {
                    try await session.prepareTranslation()
                    // 준비 후 1문장 테스트 번역으로 실제 사용 가능함을 확인.
                    _ = try await session.translations(from: [.init(sourceText: "Speed test.")])
                    emit(.status(state: "ready", reason: nil))
                    exit(0)
                } catch {
                    emit(.status(state: "error", reason: "prepare_failed: \(error)"))
                    exit(1)
                }
            }
    }
}
