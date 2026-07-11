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
