// 검증 1: 헤드리스 TranslationSession (숨김 윈도우 우회) — 최대 리스크
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
                    // session은 이 클로저 밖으로 escape 가능 — 클로저(뷰)가 살아있는 동안 유효
                    self.continuation?.resume(returning: session)
                    self.continuation = nil
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

func logErr(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)   // Dock 아이콘/포커스 탈취 금지
Task { @MainActor in
    let start = Date()
    let bridge = TranslatorBridge()
    logErr("acquiring session...")
    let session = await bridge.acquireSession(
        source: .init(identifier: "en"), target: .init(identifier: "ko"))
    logErr("session acquired in \(Date().timeIntervalSince(start))s")
    do {
        let responses = try await session.translations(from: [
            .init(sourceText: "Hello, this is a test."),
            .init(sourceText: "The animation timing looks off."),
        ])
        for r in responses { print("KO:", r.targetText) }
        logErr("translate elapsed \(Date().timeIntervalSince(start))s")
        exit(0)
    } catch {
        logErr("TRANSLATE ERROR: \(error)")
        logErr("TRANSLATE ERROR type: \(type(of: error))")
        exit(2)
    }
}
DispatchQueue.main.asyncAfter(deadline: .now() + 90) {
    logErr("TIMEOUT: no result within 90s")
    exit(3)
}
app.run()
