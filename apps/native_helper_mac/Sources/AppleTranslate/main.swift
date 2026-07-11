import AppKit
import AppleTranslateKit
import Foundation

let args = CommandLine.arguments
guard args.count >= 2 else {
    emit(.status(state: "error", reason: "usage: apple-live-translate <live|transcribe-file|translate-batch|prepare-translation>"))
    exit(2)
}
switch args[1] {
case "translate-batch":
    guard #available(macOS 15.0, *) else {
        emit(.status(state: "error", reason: "unsupported_os"))
        exit(3)
    }
    let app = NSApplication.shared
    app.setActivationPolicy(.prohibited)
    Task { exit(await runBatchTranslate()) }
    app.run()
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
case "prepare-translation":
    // 저지연 번역 팩 1회 다운로드 — 유일하게 포커스를 갖는 보이는 창을 띄운다.
    guard #available(macOS 15.0, *) else {
        emit(.status(state: "error", reason: "unsupported_os"))
        exit(3)
    }
    runPrepareTranslation()   // app.run() 블로킹 — 종료는 View 안에서 exit()
case "live":
    guard #available(macOS 26.0, *) else {
        emit(.status(state: "error", reason: "unsupported_os"))
        exit(3)
    }
    let app = NSApplication.shared
    app.setActivationPolicy(.prohibited)
    Task { exit(await runLive()) }
    app.run()
default:
    emit(.status(state: "error", reason: "unknown_subcommand"))
    exit(2)
}
