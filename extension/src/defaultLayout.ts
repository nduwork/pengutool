import * as vscode from 'vscode';
import { LogPanel } from './logPanel';
import { MapPanel } from './mapPanel';
import { SessionNode, Snapshot } from './serveClient';
import { TerminalManager } from './terminals';
import { SessionsView } from './sessionsView';

/** Builds the reference workspace the first time the PenguPool activity view is revealed. */
export class DefaultLayout implements vscode.Disposable {
  private readonly visibility: vscode.Disposable;
  private snapshot?: Snapshot;
  private opened = false;
  private terminalOpening = false;
  private terminalShown = false;
  private attemptedFor = '';  // session set the default terminal already failed for: no 1 Hz retry loop

  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly tree: SessionsView,
    private readonly terminals: TerminalManager,
  ) {
    this.visibility = tree.onDidChangeVisibility(({ visible }) => {
      if (visible) { void this.open(); }
    });
    if (tree.visible) { void this.open(); }
  }

  update(snapshot: Snapshot): void {
    this.snapshot = snapshot;
    if (this.opened) { void this.showTerminal(); }
  }

  private async open(): Promise<void> {
    if (this.opened) { return; }
    this.opened = true;

    // Creating the map in column one makes the log's Beside column deterministic.
    MapPanel.show(this.context, this.snapshot, vscode.ViewColumn.One);
    LogPanel.show(this.snapshot, vscode.ViewColumn.Beside);
    await vscode.commands.executeCommand('workbench.action.positionPanelBottom');
    await this.showTerminal();
  }

  private async showTerminal(): Promise<void> {
    if (this.terminalShown || this.terminalOpening || !this.snapshot) { return; }
    const key = this.snapshot.roots.map((root) => root.id).sort().join();
    if (key && key === this.attemptedFor) { return; }  // retry only once the sessions change
    this.terminalOpening = true;
    try {
      this.terminalShown = await this.terminals.showDefault(this.snapshot.roots, this.tree.selection[0]);
      if (!this.terminalShown) { this.attemptedFor = key; }
    } finally {
      this.terminalOpening = false;
    }
  }

  dispose(): void {
    this.visibility.dispose();
  }
}
