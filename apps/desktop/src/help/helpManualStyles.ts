// === ANCHOR: HELP_MANUAL_STYLES_START ===
import type { CSSProperties } from "react";

export const helpManualStyles: Record<string, CSSProperties> = {
  panel: {
    maxWidth: 1180,
    margin: "0 auto",
    padding: 32,
  },
  hero: {
    marginBottom: 22,
    padding: 26,
    borderRadius: 30,
    color: "#f8fafc",
    background: "linear-gradient(135deg, rgba(14,116,144,.34), rgba(15,23,42,.88))",
    border: "1px solid rgba(125,211,252,.28)",
    boxShadow: "0 24px 80px rgba(0,0,0,.24)",
  },
  eyebrow: {
    margin: "0 0 8px",
    color: "#7dd3fc",
    fontSize: 12,
    fontWeight: 950,
    letterSpacing: ".08em",
    textTransform: "uppercase",
  },
  title: {
    margin: 0,
    fontSize: 38,
    letterSpacing: "-.04em",
  },
  intro: {
    maxWidth: 800,
    margin: "10px 0 0",
    color: "#cbd5e1",
    fontSize: 16,
    lineHeight: 1.65,
  },
  sectionGrid: {
    display: "grid",
    gap: 16,
  },
  section: {
    padding: 22,
    borderRadius: 26,
    background: "rgba(15,23,42,.78)",
    border: "1px solid rgba(148,163,184,.2)",
  },
  sectionTitle: {
    margin: "4px 0 8px",
    color: "#f8fafc",
    fontSize: 24,
    letterSpacing: "-.03em",
  },
  sectionSummary: {
    margin: "0 0 16px",
    color: "#cbd5e1",
    lineHeight: 1.6,
  },
  steps: {
    display: "grid",
    gap: 12,
  },
  step: {
    padding: 16,
    borderRadius: 18,
    background: "rgba(2,6,23,.58)",
    border: "1px solid rgba(148,163,184,.16)",
  },
  stepTitle: {
    margin: "0 0 6px",
    color: "#e0f2fe",
    fontSize: 16,
  },
  stepBody: {
    margin: 0,
    color: "#cbd5e1",
    lineHeight: 1.65,
  },
  command: {
    margin: "12px 0 0",
    padding: 13,
    borderRadius: 14,
    overflowX: "auto",
    color: "#bfdbfe",
    background: "#020617",
    border: "1px solid rgba(125,211,252,.2)",
    fontSize: 12,
    lineHeight: 1.6,
    whiteSpace: "pre-wrap",
  },
};
// === ANCHOR: HELP_MANUAL_STYLES_END ===
