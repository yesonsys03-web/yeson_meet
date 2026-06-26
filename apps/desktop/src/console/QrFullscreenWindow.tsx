// === ANCHOR: QR_FULLSCREEN_WINDOW_START ===
import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { closeCurrentQrWindow, isEditableTarget, qrWindowParams } from "./useQrFullscreenShortcut";

export function QrFullscreenWindow() {
  const { url } = qrWindowParams();
  const [qrSvg, setQrSvg] = useState("");

  useEffect(() => {
    if (!url) return;
    let active = true;
    QRCode.toString(url, {
      type: "svg",
      errorCorrectionLevel: "M",
      margin: 2,
      width: 1024,
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
  }, [url]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        void closeCurrentQrWindow();
        return;
      }
      // event.code is keyboard-layout-independent, so the Korean ㅂ (same physical key as q) is covered automatically without IME issues.
      if (event.code !== "KeyQ") return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.repeat || isEditableTarget(event.target)) return;
      event.preventDefault();
      void closeCurrentQrWindow();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <main
      style={qrWindowStyles.page}
      onClick={() => void closeCurrentQrWindow()}
      title="클릭 또는 q(ㅂ)로 닫기"
    >
      <div style={qrWindowStyles.qrFrame} dangerouslySetInnerHTML={{ __html: qrSvg }} aria-label="QR code for viewer URL" />
      <p style={qrWindowStyles.url}>{url}</p>
    </main>
  );
}

const qrWindowStyles: Record<string, React.CSSProperties> = {
  page: {
    width: "100vw",
    height: "100vh",
    margin: 0,
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    alignItems: "center",
    gap: 16,
    padding: "2vh 4vw",
    background: "#020617",
    color: "#f8fafc",
    fontFamily: "Aptos, 'Segoe UI', sans-serif",
    cursor: "pointer",
  },
  qrFrame: {
    width: "min(86vw, 66vh)",
    height: "min(86vw, 66vh)",
    flexShrink: 0,
    display: "grid",
    placeItems: "center",
    padding: "min(3vw, 3vh)",
    borderRadius: 24,
    background: "#f8fafc",
    boxShadow: "0 18px 42px rgba(2,6,23,.34)",
  },
  url: {
    margin: 0,
    flexShrink: 0,
    fontSize: 18,
    color: "#bae6fd",
    overflowWrap: "anywhere",
    textAlign: "center",
    maxWidth: "90vw",
  },
};
// === ANCHOR: QR_FULLSCREEN_WINDOW_END ===
