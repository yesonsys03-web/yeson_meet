// === ANCHOR: VIEWER_QR_PANEL_START ===
import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { consoleStyles } from "./consoleStyles";
import { isEditableTarget, openQrWindow } from "./useQrFullscreenShortcut";

type ViewerQrPanelProps = {
  viewerUrl: string;
};

export function ViewerQrPanel({ viewerUrl }: ViewerQrPanelProps) {
  const [qrSvg, setQrSvg] = useState("");

  useEffect(() => {
    let active = true;
    QRCode.toString(viewerUrl, {
      type: "svg",
      errorCorrectionLevel: "M",
      margin: 2,
      width: 120,
      color: {
        dark: "#020617",
        light: "#f8fafc",
      },
    }).then((svg) => {
      if (active) setQrSvg(svg);
    });
    return () => {
      active = false;
    };
  }, [viewerUrl]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      // event.code is keyboard-layout-independent, so the Korean ㅂ (same physical key as q) is covered automatically without IME issues.
      if (event.code !== "KeyQ") return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.repeat || isEditableTarget(event.target)) return;
      event.preventDefault();
      void openQrWindow(viewerUrl);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [viewerUrl]);

  return (
    <section style={consoleStyles.qrPanel} aria-label="Viewer QR code">
      <div style={consoleStyles.qrCopy}>
        <strong>Viewer QR</strong>
        <span>클릭/q 확대</span>
      </div>
      <div
        style={{ ...consoleStyles.qrFrame, cursor: "pointer" }}
        dangerouslySetInnerHTML={{ __html: qrSvg }}
        aria-label="QR code for viewer URL"
        role="button"
        tabIndex={0}
        title="클릭 또는 q(ㅂ)로 확대"
        onClick={() => void openQrWindow(viewerUrl)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            void openQrWindow(viewerUrl);
          }
        }}
      />
    </section>
  );
}
// === ANCHOR: VIEWER_QR_PANEL_END ===
