import AppKit
import SwiftUI
import Translation

/// 헤드리스 TranslationSession 확보용 브리지.
///
/// 스파이크(2026-07-11 apple-spike-notes)에서 확정된 패턴: 숨김 NSWindow +
/// NSHostingView(rootView: Color.clear.translationTask(...)) 로 SwiftUI의
/// `.translationTask`가 세션을 발화하도록 유도한다. RunLoop 단독으로는 발화하지
/// 않으며 NSApplication.shared + app.run() 이 필요하다(main.swift에서 1회 수행).
/// 윈도우/호스팅뷰는 강한 참조로 계속 유지해야 세션이 유효하다.
@available(macOS 15.0, *)
@MainActor
public final class TranslatorBridge {
    private var window: NSWindow?
    private var continuation: CheckedContinuation<TranslationSession, Never>?

    public init() {}

    public func acquireSession(source: Locale.Language, target: Locale.Language) async -> TranslationSession {
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
