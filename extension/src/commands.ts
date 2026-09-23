import * as vscode from 'vscode';
import { Harness, SessionNode } from './serveClient';
import { SessionsProvider } from './sessionsTree';
import { TerminalManager } from './terminals';
import { runCtl } from './util';
import { SessionsView } from './sessionsView';
import { MapPanel } from './mapPanel';
import { LogPanel } from './logPanel';

const LAST_ADD_PATH_KEY = 'pengupool.lastAddPath';

const HARNESS_LABEL: Record<Harness, string> = { cc: 'Claude Code', pi: 'pi' };

export function lastAddDirectory(context: vscode.ExtensionContext, fallback: vscode.Uri): vscode.Uri {
  const saved = context.globalState.get<string>(LAST_ADD_PATH_KEY);
  return saved ? vscode.Uri.file(saved) : fallback;
}

export async function rememberAddDirectory(context: vscode.ExtensionContext, directory: vscode.Uri): Promise<void> {
  await context.globalState.update(LAST_ADD_PATH_KEY, directory.fsPath);
}

interface Deps {
  provider: SessionsProvider;
  terminals: TerminalManager;
  tree: SessionsView;
}

function descendants(node: SessionNode): Set<string> {
  const ids = new Set<string>();
  const walk = (n: SessionNode) => { ids.add(n.id); n.children.forEach(walk); };
  walk(node);
  return ids;
}

export function registerCommands(context: vscode.ExtensionContext, d: Deps): void {
  const sel = (node?: SessionNode | string): SessionNode | undefined =>
    typeof node === 'string' ? d.provider.find(node) : node ?? d.tree.selection[0];
  const defaultDir = (): vscode.Uri =>
    vscode.workspace.workspaceFolders?.[0]?.uri ?? vscode.Uri.file(process.env.HOME || '/');

  const reg = (id: string, fn: (...a: any[]) => any) =>
    context.subscriptions.push(vscode.commands.registerCommand(id, fn));

  reg('pengupool.switch', (node?: SessionNode | string) => {
    const n = sel(node);
    if (n) {
      void d.tree.reveal(n, { select: true, focus: false, expand: true });
      MapPanel.showIfOpen()?.select(n.id);
      LogPanel.showIfOpen()?.select(n.id);
      void d.terminals.switchTo(n);
    }
  });

  reg('pengupool.quickSwitch', async () => {
    const items = d.provider.all.map((n) => ({ label: n.name, description: `${n.repo} · ${n.state}`, node: n }));
    const pick = await vscode.window.showQuickPick(items, { placeHolder: 'Switch to session…' });
    if (pick) { void vscode.commands.executeCommand('pengupool.switch', pick.node.id); }
  });

  reg('pengupool.new', async () => {
    const dir = await vscode.window.showOpenDialog({
      canSelectFolders: true, canSelectFiles: false, canSelectMany: false,
      defaultUri: defaultDir(), openLabel: 'New session here',
    });
    if (!dir?.length) { return; }
    const name = await vscode.window.showInputBox({ prompt: 'Session name', value: dir[0].path.split('/').pop() });
    if (!name) { return; }
    const placement = await vscode.window.showQuickPick([
      { label: 'Create a worktree', description: 'isolated branch for this session', value: 'worktree' },
      { label: 'Use selected folder', description: 'no new worktree · fine if a session already runs here', value: 'folder' },
    ], { placeHolder: 'Choose where to start the session' });
    if (!placement) { return; }
    const harness = await vscode.window.showQuickPick(
      (['cc', 'pi'] as Harness[]).map((value) => ({ label: HARNESS_LABEL[value], value })),
      { placeHolder: 'Choose the agent harness' },
    );
    if (!harness) { return; }
    await d.terminals.newSession(dir[0].fsPath, name, harness.value, placement.value === 'worktree');
  });

  reg('pengupool.add', async () => {
    const dir = await vscode.window.showOpenDialog({
      canSelectFolders: true, canSelectFiles: false, canSelectMany: false,
      defaultUri: lastAddDirectory(context, defaultDir()), openLabel: 'Add previous from here',
    });
    if (!dir?.length) { return; }
    await rememberAddDirectory(context, dir[0]);
    const r = await runCtl(['past', dir[0].fsPath]);
    if (r.code !== 0) { vscode.window.showErrorMessage(`PenguPool: ${r.stderr || 'no past sessions'}`); return; }
    let past: [string, string, Harness?][] = [];
    try { past = JSON.parse(r.stdout || '[]'); } catch { /* empty */ }
    if (!past.length) { vscode.window.showInformationMessage('PenguPool: no past sessions in that folder.'); return; }
    const pick = await vscode.window.showQuickPick(
      past.map(([id, title, harness = 'cc']) => ({
        label: title, description: `${HARNESS_LABEL[harness] ?? harness} · ${id.slice(0, 8)}`, id, harness,
      })),
      { placeHolder: 'Resume which past session?' },
    );
    if (!pick) { return; }
    const name = await vscode.window.showInputBox({ prompt: 'Session name', value: pick.label.slice(0, 40) });
    if (!name) { return; }
    await d.terminals.resume(dir[0].fsPath, name, pick.id, pick.harness);
  });

  reg('pengupool.group', async (node?: SessionNode) => {
    const n = sel(node);
    if (!n) { return; }
    const banned = descendants(n);
    const items: (vscode.QuickPickItem & { id: string })[] = [{ label: '(top level)', id: '' }];
    for (const c of d.provider.all) {
      // Harnesses live on separate tmux servers and never share a tree.
      if (!banned.has(c.id) && (c.harness ?? 'cc') === (n.harness ?? 'cc')) {
        items.push({ label: c.name, description: c.repo, id: c.id });
      }
    }
    const pick = await vscode.window.showQuickPick(items, { placeHolder: `Move "${n.name}" under…` });
    if (!pick) { return; }
    const r = await runCtl(['group', n.id, pick.id]);
    if (r.code !== 0) { vscode.window.showErrorMessage(`PenguPool: ${r.stderr || 'group failed'}`); }
  });

  reg('pengupool.rename', async (node?: SessionNode) => {
    const n = sel(node);
    if (!n) { return; }
    const name = await vscode.window.showInputBox({ prompt: 'New name', value: n.name });
    if (!name || name === n.name) { return; }
    // ctl picks the command: pi-intercom's /alias (the name intercom addresses) for pi, /rename for Claude
    await d.terminals.slash(n, 'rename', name);
  });

  reg('pengupool.describe', async (node?: SessionNode) => {
    const n = sel(node);
    if (!n) { return; }
    let p: { summary?: string; responsibility?: string; description_editor?: string; updated_at?: string } = {};
    try { p = JSON.parse((await runCtl(['profile', n.id])).stdout || '{}'); } catch { /* no profile yet */ }
    const by = p.description_editor ? ` (last set by ${p.description_editor} ${p.updated_at ?? ''})` : '';
    const summary = await vscode.window.showInputBox({
      prompt: `One line: what "${n.name}" owns${by}`, value: p.summary ?? '',
      validateInput: (v) => (v.length > 120 ? 'At most 120 characters' : undefined),
    });
    if (summary === undefined) { return; }
    const responsibility = await vscode.window.showInputBox({
      prompt: 'Responsibility: a short private brief for the session', value: p.responsibility ?? '',
    });
    if (responsibility === undefined) { return; }
    const r = await runCtl(['describe', n.id, '--summary', summary, '--responsibility', responsibility]);
    if (r.code !== 0) { vscode.window.showErrorMessage(`PenguPool: ${r.stderr || 'describe failed'}`); }
  });

  reg('pengupool.compact', async (node?: SessionNode) => {
    const n = sel(node);
    if (n) { await d.terminals.slash(n, 'compact'); }
  });

  reg('pengupool.restart', async (node?: SessionNode) => {
    const n = sel(node);
    if (!n) { return; }
    const ok = await vscode.window.showWarningMessage(
      `Restart "${n.name}"? Its current turn is interrupted, then it resumes in place ` +
      `with the installed ${HARNESS_LABEL[n.harness ?? 'cc']}.`, { modal: true }, 'Restart');
    if (ok) { await d.terminals.restart(n); }
  });

  reg('pengupool.close', async (node?: SessionNode) => {
    const n = sel(node);
    if (!n) { return; }
    const ok = await vscode.window.showWarningMessage(`Close "${n.name}"?`, { modal: true }, 'Close');
    if (ok) { await d.terminals.close(n); }
  });
}
