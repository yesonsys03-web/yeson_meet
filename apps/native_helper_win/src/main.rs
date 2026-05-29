// yeson-win-audio-helper: Windows WASAPI loopback → stdout 16k mono s16le PCM.
// Pure modules (ipc, pcm) build everywhere; capture is Windows-only.
mod ipc;
mod pcm;
#[cfg(windows)]
mod capture;

#[cfg(not(windows))]
fn main() {
    eprintln!("yeson-win-audio-helper is Windows-only");
    std::process::exit(2);
}

#[cfg(windows)]
fn main() {
    // Real entry implemented in Task 5.
    eprintln!("not yet implemented");
    std::process::exit(2);
}
