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
