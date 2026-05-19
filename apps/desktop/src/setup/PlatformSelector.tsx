// === ANCHOR: PLATFORM_SELECTOR_START ===
import { PLATFORM_CONFIG } from "./platformConfig";
import { styles } from "./styles";
import type { SetupPlatform } from "./types";

type PlatformSelectorProps = {
  value: SetupPlatform;
  onChange: (platform: SetupPlatform) => void;
};

const PLATFORM_ORDER: SetupPlatform[] = ["mac", "windows"];

export function PlatformSelector({ value, onChange }: PlatformSelectorProps) {
  return (
    <div style={styles.platformGroup}>
      {PLATFORM_ORDER.map((platform) => {
        const config = PLATFORM_CONFIG[platform];
        const active = platform === value;
        return (
          <button
            key={platform}
            type="button"
            onClick={() => onChange(platform)}
            style={{
              ...styles.platformButton,
              ...(active ? styles.platformButtonActive : null),
            }}
          >
            <strong>{config.label}</strong>
            <span>{config.description}</span>
          </button>
        );
      })}
    </div>
  );
}
// === ANCHOR: PLATFORM_SELECTOR_END ===
