// === ANCHOR: HELP_MANUAL_PANEL_START ===
import { helpManualSections } from "./helpManualContent";
import { helpManualStyles } from "./helpManualStyles";

export function HelpManualPanel() {
  return (
    <div style={helpManualStyles.panel}>
      <header style={helpManualStyles.hero}>
        <p style={helpManualStyles.eyebrow}>help manual</p>
        <h1 style={helpManualStyles.title}>처음 보는 사람도 따라 하는 운영 도움말</h1>
        <p style={helpManualStyles.intro}>
          서버 PC, 회의실 PC, viewer를 어떤 순서로 확인해야 하는지 짧은 카드로 정리했습니다. 새 장비나 새 절차가 생기면 helpManualContent에 섹션만 추가하면 됩니다.
        </p>
      </header>

      <div style={helpManualStyles.sectionGrid}>
        {helpManualSections.map((section) => (
          <section key={section.id} style={helpManualStyles.section}>
            <p style={helpManualStyles.eyebrow}>{section.eyebrow}</p>
            <h2 style={helpManualStyles.sectionTitle}>{section.title}</h2>
            <p style={helpManualStyles.sectionSummary}>{section.summary}</p>
            <div style={helpManualStyles.steps}>
              {section.steps.map((step) => (
                <article key={step.title} style={helpManualStyles.step}>
                  <h3 style={helpManualStyles.stepTitle}>{step.title}</h3>
                  <p style={helpManualStyles.stepBody}>{step.body}</p>
                  {step.command ? <pre style={helpManualStyles.command}>{step.command}</pre> : null}
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}
// === ANCHOR: HELP_MANUAL_PANEL_END ===
