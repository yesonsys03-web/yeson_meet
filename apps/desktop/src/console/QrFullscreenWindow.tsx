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
      // The generated SVG carries a fixed width="1024" height="1024"; left as-is
      // it ignores its frame and overflows small windows (the real clip cause).
      // Force it to fill its (sized) container instead — viewBox keeps it square.
      if (active) setQrSvg(svg.replace(/ width="\d+"/, ' width="100%"').replace(/ height="\d+"/, ' height="100%"'));
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
      <div style={qrWindowStyles.qrFrame}>
        <div style={qrWindowStyles.qrSvgWrap} dangerouslySetInnerHTML={{ __html: qrSvg }} aria-label="QR code for viewer URL" />
      </div>
      <p style={qrWindowStyles.url}>{url}</p>
    </main>
  );
}

const qrWindowStyles: Record<string, React.CSSProperties> = {
  // position:fixed + inset:0 + overflow:hidden guarantees the overlay is exactly
  // the viewport and never scrolls/clips. The QR frame shrinks to whatever space
  // remains after the caption (flex 0 1 auto + minHeight 0), capped so it never
  // exceeds the viewport on any aspect ratio/DPI; the caption never shrinks.
  page: {
    position: "fixed",
    inset: 0,
    margin: 0,
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    alignItems: "center",
    gap: "2vh",
    padding: "3vh 3vw",
    background: "#020617",
    color: "#f8fafc",
    fontFamily: "Aptos, 'Segoe UI', sans-serif",
    cursor: "pointer",
    overflow: "hidden",
  },
  qrFrame: {
    // Deterministic square, capped by BOTH width and height so it can never
    // exceed the viewport. 72vh leaves ample room for the caption + padding on
    // any window. box-sizing:border-box keeps the padding inside the cap.
    width: "min(90vw, 72vh)",
    height: "min(90vw, 72vh)",
    flexShrink: 0,
    boxSizing: "border-box",
    display: "flex",
    padding: "min(3vmin, 22px)",
    borderRadius: 24,
    background: "#f8fafc",
    boxShadow: "0 18px 42px rgba(2,6,23,.34)",
  },
  qrSvgWrap: {
    width: "100%",
    height: "100%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  url: {
    flex: "0 0 auto",
    margin: 0,
    fontSize: 16,
    color: "#bae6fd",
    overflowWrap: "anywhere",
    textAlign: "center",
    maxWidth: "94vw",
  },
};
// === ANCHOR: QR_FULLSCREEN_WINDOW_END ===
