// === ANCHOR: HELP_TYPES_START ===
export type HelpStep = {
  title: string;
  body: string;
  command?: string;
};

export type HelpSection = {
  id: string;
  eyebrow: string;
  title: string;
  summary: string;
  steps: HelpStep[];
};
// === ANCHOR: HELP_TYPES_END ===
