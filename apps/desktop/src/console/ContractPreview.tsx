// === ANCHOR: CONTRACT_PREVIEW_START ===
import { consoleStyles } from "./consoleStyles";

type ContractPreviewProps = {
  contractPreview: string;
  reportText: string;
  reportHtml?: string;
};

export function ContractPreview({ contractPreview, reportText, reportHtml }: ContractPreviewProps) {
  return (
    <aside style={consoleStyles.card}>
      <h2 style={{ marginTop: 0 }}>Server contract</h2>
      <pre style={consoleStyles.code}>{contractPreview}</pre>
      <ol style={consoleStyles.steps}>
        <li>Create session and show returned viewer URL.</li>
        <li>Run sidecar with the session ID from the setup assistant.</li>
        <li>End session and download generated Markdown report.</li>
      </ol>
      {reportHtml ? (
        <iframe
          sandbox=""
          srcDoc={reportHtml}
          style={{ width: "100%", minHeight: 320, border: "1px solid #ddd", marginTop: 14, borderRadius: 4 }}
          title="Report preview"
        />
      ) : reportText ? (
        <pre style={{ ...consoleStyles.code, marginTop: 14 }}>{reportText}</pre>
      ) : null}
    </aside>
  );
}
// === ANCHOR: CONTRACT_PREVIEW_END ===
