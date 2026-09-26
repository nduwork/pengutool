import * as vscode from 'vscode';
import { ControlSession } from './controlSession';

export interface ControlTerminalOptions {
  socket: string;  // tmux -L socket for the harness
  view: string;    // grouped view session this terminal owns
  base: string;    // session to group the view from ("pengupool")
  cwd?: string;
}

/**
 * A VS Code terminal backed by a tmux control-mode client rather than a real `tmux attach`.
 *
 * VS Code renders the pane itself, so selection, copy/paste, scrolling, and find behave like any
 * other terminal; tmux still owns the session, so it survives closing the editor. One instance is
 * reused per harness: showing another session repaints the terminal with that pane's screen.
 */
export class ControlTerminal implements vscode.Pseudoterminal {
  private readonly writeEmitter = new vscode.EventEmitter<string>();
  private readonly closeEmitter = new vscode.EventEmitter<number>();
  readonly onDidWrite = this.writeEmitter.event;
  readonly onDidClose = this.closeEmitter.event;

  /** Called when a pane paint fails (capture error or timeout), so the manager can let the next
   *  selection retry instead of leaving the terminal blank. */
  onPaintFailed?: () => void;

  private readonly session: ControlSession;
  private opened = false;
  private pendingPane?: string;
  private forcePending = false;
  private closed = false;

  constructor(options: ControlTerminalOptions) {
    // Seed as much scrollback as VS Code will keep, so native scroll and find see earlier output.
    const scrollback = vscode.workspace.getConfiguration('terminal.integrated').get<number>('scrollback', 1000);
    const historyLines = Math.min(Math.max(Math.trunc(scrollback) || 0, 0), 5000);
    this.session = new ControlSession({ ...options, cols: 80, rows: 24, historyLines });
    this.session.onData = (data) => { if (!this.closed) { this.writeEmitter.fire(data); } };
    this.session.onExit = (code) => { this.closed = true; this.closeEmitter.fire(code); };
  }

  open(dimensions: vscode.TerminalDimensions): void {
    this.opened = true;
    this.session.start();
    this.session.resize(dimensions.columns, dimensions.rows);
    if (this.pendingPane) {
      const pane = this.pendingPane;
      const force = this.forcePending;
      this.pendingPane = undefined;
      this.forcePending = false;
      void this.paint(pane, force);
    }
  }

  /** Paint `pane` in this terminal. Queued until VS Code opens it; `force` repaints an unchanged pane
   *  (a restarted session keeps its pane id but has a new screen). */
  showPane(pane: string, force = false): Promise<boolean> {
    if (this.closed) { return Promise.resolve(false); }
    if (!this.opened) { this.pendingPane = pane; this.forcePending ||= force; return Promise.resolve(true); }
    return this.paint(pane, force);
  }

  private async paint(pane: string, force: boolean): Promise<boolean> {
    const ok = await (force ? this.session.repaint(pane) : this.session.show(pane));
    if (!ok) { this.onPaintFailed?.(); }
    return ok;
  }

  handleInput(data: string): void { this.session.input(data); }

  setDimensions(dimensions: vscode.TerminalDimensions): void {
    this.session.resize(dimensions.columns, dimensions.rows);
  }

  close(): void {
    this.closed = true;
    this.session.dispose();
  }
}
