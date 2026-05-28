// === ANCHOR: NATIVE_CAPTURE_BANNER_START ===
import type { CSSProperties } from "react";
import { useState } from "react";

import { invoke } from "@tauri-apps/api/core";

import { appLogger } from "../diagnostics/appLog";
import { consoleStyles } from "./consoleStyles";
import { useNativeCaptureStatus } from "./nativeCaptureStatus";

const REASON_MESSAGE: Record<string, string> = {
  permission_denied:
    "회의 오디오 캡처에 화면 기록(Screen Recording) 권한이 필요합니다. 설정에서 허용한 뒤 회의를 다시 시작하세요.",
  start_failed: "오디오 캡처를 시작하지 못했습니다. 잠시 후 회의를 다시 시작해 보세요.",
};

const bannerStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  padding: "10px 14px",
  margin: "8px 0",
  borderRadius: 8,
  border: "1px solid #d98a00",
  background: "#3a2c08",
  color: "#ffd27a",
  fontSize: 13,
};

export function NativeCaptureBanner() {
  const status = useNativeCaptureStatus();
  const [dismissedId, setDismissedId] = useState(0);

  if (!status || status.id <= dismissedId) return null;

  const isPermission = status.reason === "permission_denied";
  const message = REASON_MESSAGE[status.reason] ?? `오디오 캡처 오류: ${status.reason}`;

  return (
    <div role="alert" style={bannerStyle}>
      <span>{message}</span>
      <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
        {isPermission ? (
          <button
            type="button"
            style={consoleStyles.action}
            onClick={() => {
              void invoke("open_screen_recording_settings").catch((error) =>
                appLogger.warn("native", "failed to open Screen Recording settings", {
                  detail: error instanceof Error ? error.message : String(error),
                }),
              );
            }}
          >
            시스템 설정 열기
          </button>
        ) : null}
        <button type="button" style={consoleStyles.mutedAction} onClick={() => setDismissedId(status.id)}>
          닫기
        </button>
      </div>
    </div>
  );
}
// === ANCHOR: NATIVE_CAPTURE_BANNER_END ===
