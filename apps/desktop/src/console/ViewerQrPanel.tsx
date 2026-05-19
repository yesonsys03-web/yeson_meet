// === ANCHOR: VIEWER_QR_PANEL_START ===
import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { consoleStyles } from "./consoleStyles";

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

  return (
    <section style={consoleStyles.qrPanel} aria-label="Viewer QR code">
      <div style={consoleStyles.qrCopy}>
        <strong>Viewer QR</strong>
        <span>Phone scan ready</span>
      </div>
      <div
        style={consoleStyles.qrFrame}
        dangerouslySetInnerHTML={{ __html: qrSvg }}
        aria-label="QR code for viewer URL"
      />
    </section>
  );
}
// === ANCHOR: VIEWER_QR_PANEL_END ===
