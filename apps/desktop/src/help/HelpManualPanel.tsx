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
          회의 자막을 켜고, 참석자에게 보여주고, 끝나고 기록으로 남기는 방법을 순서대로 정리했어요. 위에서부터 차례대로 따라 하면 됩니다. 막히면 맨 아래 '안 될 때'를 보세요.
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
