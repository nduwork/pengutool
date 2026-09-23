import * as vscode from 'vscode';
import { Harness, SessionNode } from './serveClient';
import { runCtl } from './util';

const LEGACY_STATE_KEY = 'pengupool.attachedTerminals';

function flatten(roots: SessionNode[]): SessionNode[] {
  const out: SessionNode[] = [];
  const walk = (n: SessionNode) => { out.push(n); n.children.forEach(walk); };
  roots.forEach(walk);
  return out;
}

function newViewId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** One grouped tmux client per harness: a view can only switch within its own tmux server. */
interface Slot {
  name: string;
  viewId: string;
  term?: vscode.Terminal;
  currentId?: string;
  pending?: { pane: string; cwd: string };
}

/** One VS Code terminal per harness, each backed directly by one grouped tmux client. */
export class TerminalManager implements vscode.Disposable {
  private readonly slots = new Map<Harness, Slot>([
    ['cc', { name: 'PenguPool', viewId: newViewId() }],
    ['pi', { name: 'PenguPool · pi', viewId: newViewId() }],
  ]);
  private roots: SessionNode[] = [];
  private readonly disp: vscode.Disposable[] = [];
  private readonly legacyNames = new Set<string>();
  private readonly sessionNames = new Set<string>();

  constructor(private readonly ctx: vscode.ExtensionContext) {
    this.removeLegacyTerminals();
    this.disp.push(vscode.window.onDidOpenTerminal((terminal) => this.removeLegacyTerminal(terminal)));
    this.disp.push(vscode.window.onDidChangeActiveTerminal(() => this.updateActiveContext()));
    this.disp.push(vscode.window.onDidCloseTerminal((terminal) => {
      for (const slot of this.slots.values()) {
        if (terminal === slot.term) {
          slot.term = undefined;
          slot.currentId = undefined;
          slot.pending = undefined;
        }
      }
      this.updateActiveContext();
    }));
    this.updateActiveContext();
  }

  private slot(harness?: string): Slot {
    return this.slots.get(harness as Harness) ?? this.slots.get('cc')!;
  }

  private owns(terminal: vscode.Terminal): boolean {
    return [...this.slots.values()].some((slot) => terminal === slot.term || terminal.name === slot.name);
  }

  private updateActiveContext(): void {
    const active = vscode.window.activeTerminal;
    const owned = !!active && this.slots.get('cc')?.term === active;
    void vscode.commands.executeCommand('setContext', 'pengupool.claudeTerminal', owned);
  }

  /** Old builds restored shell-backed terminals by display name; remove those stale zsh views. */
  private removeLegacyTerminals(): void {
    const saved = this.ctx.workspaceState.get<Record<string, string>>(LEGACY_STATE_KEY, {});
    const names = Object.values(saved).filter((value): value is string => typeof value === 'string');
    names.forEach((name) => this.legacyNames.add(name));
    for (const terminal of vscode.window.terminals) {
      this.removeLegacyTerminal(terminal);
    }
    if (names.length) { void this.ctx.workspaceState.update(LEGACY_STATE_KEY, undefined); }
  }

  private removeLegacyTerminal(terminal: vscode.Terminal): void {
    if (!this.owns(terminal) && (this.legacyNames.has(terminal.name) || this.sessionNames.has(terminal.name))) {
      terminal.dispose();
    }
  }

  reconcile(roots: SessionNode[]): void {
    this.roots = roots;
    // VS Code can restore disconnected terminals after extension activation. Old PenguPool builds
    // named each shell after its session; remove those once the live session names are known.
    this.sessionNames.clear();
    flatten(roots).forEach((node) => this.sessionNames.add(node.name));
    for (const terminal of vscode.window.terminals) {
      this.removeLegacyTerminal(terminal);
    }
    for (const [harness, slot] of this.slots) {
      if (!slot.pending) { continue; }
      // pane ids are per tmux server: a cc %3 and a pi %3 are different panes
      const hit = flatten(roots).find((node) => (node.harness ?? 'cc') === harness &&
        node.tmux_pane === slot.pending?.pane && node.cwd === slot.pending?.cwd,
      );
      if (hit) {
        slot.currentId = hit.id;
        slot.pending = undefined;
      }
    }
  }

  private usable(slot: Slot): boolean {
    return !!slot.term && slot.term.exitStatus === undefined;
  }

  private open(stdout: string, id?: string): boolean {
    let view: { command: string; pane: string; cwd: string; harness?: string };
    try {
      view = JSON.parse(stdout);
      if (!view || typeof view.command !== 'string' || !view.command ||
          typeof view.pane !== 'string' || !view.pane || typeof view.cwd !== 'string') {
        throw new Error('invalid launch metadata');
      }
    } catch {
      vscode.window.showErrorMessage('PenguPool: invalid launch metadata; update the Python CLI.');
      return false;
    }

    const slot = this.slot(view.harness);
    slot.term?.dispose();
    slot.term = vscode.window.createTerminal({
      name: slot.name,
      shellPath: '/bin/sh',
      shellArgs: ['-lc', `exec ${view.command}`],
      cwd: view.cwd || undefined,
      env: { ...process.env },
      strictEnv: true,
      isTransient: true,
    });
    // Removing restored legacy tabs can make VS Code create a blank default shell to keep the
    // visible panel populated. Close only untouched default shells once our real work pane exists.
    const defaultShells = new Set(['sh', 'bash', 'zsh', 'fish', 'dash', 'ksh']);
    for (const terminal of vscode.window.terminals) {
      if (!this.owns(terminal) && defaultShells.has(terminal.name) && !terminal.state.isInteractedWith) {
        terminal.dispose();
      }
    }
    slot.currentId = id;
    slot.pending = id ? undefined : { pane: view.pane, cwd: view.cwd };
    slot.term.show();
    this.updateActiveContext();
    if (!id) { this.reconcile(this.roots); }
    return true;
  }

  /** Switch the harness's extension terminal to a session without restarting that session. */
  async switchTo(node: SessionNode, offerAdopt = true): Promise<boolean> {
    const slot = this.slot(node.harness);
    if (this.usable(slot) && slot.currentId === node.id) {
      slot.term!.show();
      return true;
    }

    if (this.usable(slot)) {
      const selected = await runCtl(['select', node.id, slot.viewId]);
      if (selected.code === 0) {
        slot.currentId = node.id;
        slot.pending = undefined;
        slot.term!.show();
        return true;
      }
      if (selected.code === 2 && offerAdopt) { return this.offerAdopt(node); }
      // Code 3 means the grouped view disappeared. Recreate the client below.
      if (selected.code !== 3 && selected.code !== 2) {
        vscode.window.showErrorMessage(`PenguPool: ${selected.stderr || 'could not switch session'}`);
        return false;
      }
      slot.term!.dispose();
      slot.term = undefined;
      slot.currentId = undefined;
    }

    const attached = await runCtl(['--json', 'attach', node.id, node.name, slot.viewId]);
    if (attached.code === 0 && attached.stdout) { return this.open(attached.stdout, node.id); }
    if (attached.code === 2 && offerAdopt) { return this.offerAdopt(node); }
    if (attached.code !== 2) {
      vscode.window.showErrorMessage(`PenguPool: ${attached.stderr || 'could not attach session'}`);
    }
    return false;
  }

  private async offerAdopt(node: SessionNode): Promise<boolean> {
    const pick = await vscode.window.showWarningMessage(
      `"${node.name}" is running outside the PenguPool tmux server. To view it here it must be adopted: ` +
      `its current run is stopped (interrupting its turn) and resumed in a tmux terminal.`,
      { modal: true }, 'Adopt (stop & re-host)',
    );
    return pick ? this.adopt(node) : false;
  }

  async showDefault(roots: SessionNode[], selected?: SessionNode): Promise<boolean> {
    const nodes = flatten(roots);
    const ids = new Set([...this.slots.values()].map((slot) => slot.currentId));
    const current = nodes.find((candidate) => ids.has(candidate.id));
    const node = current ?? selected ?? nodes[0];
    return node ? this.switchTo(node, false) : false;
  }

  async newSession(cwd: string, name: string, harness: Harness = 'cc', createWorktree = true): Promise<void> {
    const location = createWorktree ? 'worktree' : 'folder';
    const result = await runCtl(['--json', 'new', cwd, name, this.slot(harness).viewId, harness, location]);
    if (result.code === 0 && result.stdout) { this.open(result.stdout); }
    else { vscode.window.showErrorMessage(`PenguPool: ${result.stderr || 'could not start session'}`); }
  }

  async resume(cwd: string, name: string, sessionId: string, harness: Harness = 'cc'): Promise<void> {
    const live = flatten(this.roots).find((node) => node.id === sessionId);
    if (live) { await this.switchTo(live); return; }
    const result = await runCtl(['--json', 'resume', cwd, name, sessionId, this.slot(harness).viewId]);
    if (result.code === 0 && result.stdout) { this.open(result.stdout, sessionId); }
    else { vscode.window.showErrorMessage(`PenguPool: ${result.stderr || 'could not resume session'}`); }
  }

  async adopt(node: SessionNode): Promise<boolean> {
    const result = await runCtl(['--json', 'adopt', node.id, this.slot(node.harness).viewId]);
    if (result.code === 0 && result.stdout) { return this.open(result.stdout, node.id); }
    vscode.window.showErrorMessage(`PenguPool: ${result.stderr || 'could not adopt session'}`);
    return false;
  }

  /** Stop a session and resume it in its own pane; the view stays attached (same pane). */
  async restart(node: SessionNode): Promise<boolean> {
    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `PenguPool: restarting ${node.name}…` },
      () => runCtl(['--json', 'restart', node.id, this.slot(node.harness).viewId]));
    if (result.code !== 0 || !result.stdout) {
      vscode.window.showErrorMessage(`PenguPool: ${result.stderr || 'could not restart session'}`);
      return false;
    }
    // The pane id is unchanged, so a terminal already on it stays attached. Otherwise open the returned
    // view: switching by id could run before the new process is registered and drop the terminal.
    const slot = this.slot(node.harness);
    if (this.usable(slot) && slot.currentId === node.id) { slot.term!.show(); return true; }
    return this.open(result.stdout, node.id);
  }

  async close(node: SessionNode): Promise<void> {
    const result = await runCtl(['close', node.id]);
    if (result.code !== 0) {
      vscode.window.showErrorMessage(`PenguPool: ${result.stderr || 'could not close session'}`);
      return;
    }
    const slot = this.slot(node.harness);
    if (slot.currentId === node.id) { slot.currentId = undefined; }
  }

  async send(node: SessionNode, text: string): Promise<boolean> {
    if (!(await this.switchTo(node))) { return false; }
    this.slot(node.harness).term!.sendText(text, true);
    return true;
  }

  dispose(): void {
    this.disp.forEach((disposable) => disposable.dispose());
    this.slots.forEach((slot) => slot.term?.dispose());
    void vscode.commands.executeCommand('setContext', 'pengupool.claudeTerminal', false);
  }
}
