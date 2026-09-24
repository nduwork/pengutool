/** Shared status language for the Sessions list and map. Color supplements the symbol and label. */
export const SESSION_STATES: Record<string, { symbol: string; label: string }> = {
  active: { symbol: '●', label: 'Active' },
  waiting: { symbol: '◷', label: 'Waiting' },
  stale: { symbol: '○', label: 'Stale' },
  blocked: { symbol: '?', label: 'Approval' },
};

export const SESSION_STATE_CSS = `
  .state-active { --state-color: var(--vscode-testing-iconPassed, #2e7d32); }
  .state-waiting { --state-color: var(--vscode-editorWarning-foreground, #a66b00); }
  .state-stale { --state-color: var(--vscode-descriptionForeground, #777); }
  .state-blocked { --state-color: var(--vscode-errorForeground, #c42b1c); }
`;

/** Context-window use by level: green below 30%, yellow below 60%, red from 60% up. */
export const CTX_LEVEL_JS = `function ctxLevel(p){ return p < 30 ? 'ctx-low' : p < 60 ? 'ctx-mid' : 'ctx-high'; }`;
export const CTX_LEVEL_CSS = `
  .ctx-low { color: var(--vscode-charts-green, #388a34); }
  .ctx-mid { color: var(--vscode-charts-yellow, #cca700); }
  .ctx-high { color: var(--vscode-charts-red, #e51400); }
`;
