import * as vscode from 'vscode';

/** Static help pinned below the Sessions tree — the controls cheat sheet. */
type Hint = { label: string; description?: string; icon?: string; children?: Hint[] };

const HINTS: Hint[] = [
  { label: 'Sessions', children: [
    { label: '⏎ / click', description: 'open · focus work session' },
    { label: 'n', description: 'choose worktree · Claude Code or pi' },
    { label: 'a', description: 'add previous Claude Code or pi session' },
    { label: 'g / drag', description: 'group under session · empty = top level' },
    { label: 'r / x', description: 'rename · close session' },
    { label: 'right-click', description: 'all session actions' },
    { label: 'Shift+R', description: 'restart & resume · picks up a Claude Code / pi update' },
    { label: 'c / /compact', description: 'compact · preserve group placement' },
    { label: 'Shift+Enter', description: 'prompt newline in Claude terminal' },
  ] },
  { label: 'Views', children: [
    { label: 'Map', description: 'click node · select work session', icon: 'type-hierarchy' },
    { label: 'Log', description: 'messages in selected session tree', icon: 'output' },
    { label: 'workflow / context', description: 'map cards refresh phase chain · context use' },
  ] },
];

export class HintsProvider implements vscode.TreeDataProvider<Hint> {
  getChildren(element?: Hint): Hint[] { return element?.children ?? HINTS; }
  getTreeItem(hint: Hint): vscode.TreeItem {
    const state = hint.children
      ? vscode.TreeItemCollapsibleState.Expanded
      : vscode.TreeItemCollapsibleState.None;
    const item = new vscode.TreeItem(hint.label, state);
    item.description = hint.description;
    if (hint.icon) { item.iconPath = new vscode.ThemeIcon(hint.icon); }
    return item;
  }
}
