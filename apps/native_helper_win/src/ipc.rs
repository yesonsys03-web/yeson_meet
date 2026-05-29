// === ANCHOR: WIN_IPC_START ===
use std::io::{self, Write};

/// A flushable byte sink. Abstracted so tests use an in-memory buffer
/// and production uses locked stdout/stderr.
pub trait ByteSink {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()>;
    fn flush(&mut self) -> io::Result<()>;
}

impl<W: Write> ByteSink for W {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        Write::write_all(self, data)
    }
    fn flush(&mut self) -> io::Result<()> {
        Write::flush(self)
    }
}

/// stdout = PCM binary stream, stderr = JSON line events.
pub struct Ipc<D: ByteSink, C: ByteSink> {
    data: D,
    control: C,
}

impl<D: ByteSink, C: ByteSink> Ipc<D, C> {
    pub fn new(data: D, control: C) -> Self {
        Self { data, control }
    }

    /// Write one PCM chunk to the data sink and flush immediately.
    /// Returns the io::Result so the caller can detect a broken pipe.
    pub fn emit_chunk(&mut self, chunk: &[u8]) -> io::Result<()> {
        self.data.write_all(chunk)?;
        self.data.flush()
    }

    /// Emit a `{"event":name,"payload":...}` line + '\n' to the control sink.
    pub fn emit_event(&mut self, name: &str, payload: serde_json::Value) {
        let obj = serde_json::json!({ "event": name, "payload": payload });
        // Best-effort: if stderr is gone there is nothing useful to do.
        if let Ok(mut line) = serde_json::to_vec(&obj) {
            line.push(b'\n');
            let _ = self.control.write_all(&line);
            let _ = self.control.flush();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn emit_chunk_passes_bytes_through() {
        let mut data = Vec::new();
        let mut ctrl = Vec::new();
        {
            let mut ipc = Ipc::new(&mut data, &mut ctrl);
            ipc.emit_chunk(&[1, 2, 3, 4]).unwrap();
        }
        assert_eq!(data, vec![1, 2, 3, 4]);
        assert!(ctrl.is_empty());
    }

    #[test]
    fn emit_event_writes_one_json_line() {
        let mut data = Vec::new();
        let mut ctrl = Vec::new();
        {
            let mut ipc = Ipc::new(&mut data, &mut ctrl);
            ipc.emit_event("started", serde_json::json!({"source_sample_rate": 48000}));
        }
        assert!(data.is_empty());
        assert_eq!(*ctrl.last().unwrap(), b'\n');
        let v: serde_json::Value = serde_json::from_slice(&ctrl[..ctrl.len() - 1]).unwrap();
        assert_eq!(v["event"], "started");
        assert_eq!(v["payload"]["source_sample_rate"], 48000);
    }

    #[test]
    fn fatal_event_shape_matches_contract() {
        let mut data = Vec::new();
        let mut ctrl = Vec::new();
        {
            let mut ipc = Ipc::new(&mut data, &mut ctrl);
            ipc.emit_event(
                "fatal",
                serde_json::json!({"reason": "wasapi_init_failed", "detail": "x"}),
            );
        }
        let v: serde_json::Value = serde_json::from_slice(&ctrl[..ctrl.len() - 1]).unwrap();
        assert_eq!(v["event"], "fatal");
        assert_eq!(v["payload"]["reason"], "wasapi_init_failed");
    }
}
// === ANCHOR: WIN_IPC_END ===
