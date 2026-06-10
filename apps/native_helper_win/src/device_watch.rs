// === ANCHOR: WIN_DEVICE_WATCH_START ===
//! Pure default-device-change decision (no cpal/windows types, spec §5).
//! Inputs: the device we're currently capturing, the freshly re-queried default,
//! and a monotonic `now_ms`. Output: rebuild the loopback on the new default, or
//! ignore. A min-rebuild-interval throttle suppresses flapping (two devices
//! trading the default back and forth).

#[derive(Debug, PartialEq, Eq)]
pub enum Decision {
    /// Default differs from the active device and throttle allows → rebuild now.
    Rebuild,
    /// Same device, no default, or within throttle window → do nothing.
    Ignore,
}

pub struct DeviceWatcher {
    min_rebuild_interval_ms: u64,
    last_rebuild_ms: Option<u64>,
}

impl DeviceWatcher {
    pub fn new(min_rebuild_interval_ms: u64) -> Self {
        Self {
            min_rebuild_interval_ms,
            last_rebuild_ms: None,
        }
    }

    /// `active` = name of the device currently being captured.
    /// `polled` = freshly re-queried default output name (None if no default).
    /// `now_ms` = monotonic milliseconds.
    /// On a returned `Rebuild`, the throttle clock is stamped to `now_ms`.
    pub fn decide(&mut self, active: &str, polled: Option<&str>, now_ms: u64) -> Decision {
        let Some(polled) = polled else {
            return Decision::Ignore; // no default device to switch to
        };
        if polled == active {
            return Decision::Ignore; // unchanged
        }
        if let Some(last) = self.last_rebuild_ms {
            if now_ms.saturating_sub(last) < self.min_rebuild_interval_ms {
                return Decision::Ignore; // anti-flap throttle
            }
        }
        self.last_rebuild_ms = Some(now_ms);
        Decision::Rebuild
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const THROTTLE: u64 = 5_000;

    #[test]
    fn rebuilds_when_default_differs() {
        let mut w = DeviceWatcher::new(THROTTLE);
        assert_eq!(w.decide("Speakers", Some("Headphones"), 1_000), Decision::Rebuild);
    }

    #[test]
    fn ignores_when_same_device() {
        let mut w = DeviceWatcher::new(THROTTLE);
        assert_eq!(w.decide("Speakers", Some("Speakers"), 1_000), Decision::Ignore);
    }

    #[test]
    fn ignores_when_no_default() {
        let mut w = DeviceWatcher::new(THROTTLE);
        assert_eq!(w.decide("Speakers", None, 1_000), Decision::Ignore);
    }

    #[test]
    fn throttles_rapid_reswitch() {
        let mut w = DeviceWatcher::new(THROTTLE);
        // First switch at t=1000 → Rebuild (stamps 1000).
        assert_eq!(w.decide("Speakers", Some("Headphones"), 1_000), Decision::Rebuild);
        // A different default 2s later is within the 5s window → Ignore.
        assert_eq!(w.decide("Headphones", Some("Speakers"), 3_000), Decision::Ignore);
    }

    #[test]
    fn rebuilds_again_after_throttle_window() {
        let mut w = DeviceWatcher::new(THROTTLE);
        assert_eq!(w.decide("Speakers", Some("Headphones"), 1_000), Decision::Rebuild);
        // 6s later (> 5s window) → Rebuild allowed again.
        assert_eq!(w.decide("Headphones", Some("Speakers"), 7_000), Decision::Rebuild);
    }

    #[test]
    fn immediate_repoll_after_rebuild_is_ignored() {
        let mut w = DeviceWatcher::new(THROTTLE);
        assert_eq!(w.decide("Speakers", Some("Headphones"), 1_000), Decision::Rebuild);
        // Same poll value before `active` is updated by main → still throttled.
        assert_eq!(w.decide("Speakers", Some("Headphones"), 1_200), Decision::Ignore);
    }
}
// === ANCHOR: WIN_DEVICE_WATCH_END ===
