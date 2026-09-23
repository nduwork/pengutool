import * as vscode from 'vscode';
import { SessionNode, Snapshot } from './serveClient';
import { runCtl } from './util';
import { SESSION_STATES } from './sessionState';

const SESSION_MIME = 'application/vnd.code.tree.pengupoolsessions';

/** Native sidebar tree of the session hierarchy. Fed by ServeClient snapshots; mouse, keyboard and
 *  pane resize are all VS Code-native here (no xterm.js mouse involvement). */
export class SessionsProvider implements vscode.TreeDataProvider<SessionNode>, vscode.TreeDragAndDropController<SessionNode> {
  readonly dragMimeTypes = [SESSION_MIME];
  readonly dropMimeTypes = [SESSION_MIME];
  private readonly _onDidChange = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  private roots: SessionNode[] = [];
  private parent = new Map<string, SessionNode | undefined>();
  private byId = new Map<string, SessionNode>();

  update(snap: Snapshot): void {
    this.roots = snap.roots;
    this.parent.clear();
    this.byId.clear();
    const walk = (n: SessionNode, p?: SessionNode) => {
      this.parent.set(n.id, p);
      this.byId.set(n.id, n);
      n.children.forEach((c) => walk(c, n));
    };
    snap.roots.forEach((r) => walk(r, undefined));
    this._onDidChange.fire();
  }

  get all(): SessionNode[] {
    return [...this.byId.values()];
  }

  find(id: string): SessionNode | undefined {
    return this.byId.get(id);
  }

  getChildren(node?: SessionNode): SessionNode[] {
    return node ? node.children : this.roots;
  }

  getParent(node: SessionNode): SessionNode | undefined {
    return this.parent.get(node.id);
  }

  getTreeItem(node: SessionNode): vscode.TreeItem {
    const item = new vscode.TreeItem(
      `${SESSION_STATES[node.state]?.symbol ?? '·'} ${node.name}`,
      node.children.length ? vscode.TreeItemCollapsibleState.Expanded : vscode.TreeItemCollapsibleState.None,
    );
    const ctx = node.ctx_pct != null ? ` · ${node.ctx_pct}% ctx` : '';
    item.description = `${node.harness === 'pi' ? 'pi · ' : ''}${node.repo}${ctx}`;
    item.tooltip = [node.name, node.cwd, node.status].filter(Boolean).join('\n');
    item.contextValue = 'pengupoolSession';
    item.id = node.id;
    item.command = { command: 'pengupool.switch', title: 'Open', arguments: [node] };
    return item;
  }

  handleDrag(source: readonly SessionNode[], dataTransfer: vscode.DataTransfer): void {
    dataTransfer.set(SESSION_MIME, new vscode.DataTransferItem(source.map((node) => node.id)));
  }

  async handleDrop(target: SessionNode | undefined, dataTransfer: vscode.DataTransfer): Promise<void> {
    const item = dataTransfer.get(SESSION_MIME);
    if (!item) { return; }
    let ids: string[];
    try {
      ids = Array.isArray(item.value) ? item.value : JSON.parse(await item.asString());
    } catch {
      return;
    }
    for (const id of ids) {
      await this.group(id, target?.id ?? '');
    }
  }

  async group(sourceId: string, targetId: string): Promise<void> {
    const source = this.find(sourceId);
    const target = targetId ? this.find(targetId) : undefined;
    if (!source || (targetId && !target)) { return; }
    if (target && this.contains(source, target.id)) {
      vscode.window.showWarningMessage(`PenguPool: cannot move "${source.name}" under itself or its child.`);
      return;
    }
    const result = await runCtl(['group', source.id, target?.id ?? '']);
    if (result.code !== 0) {
      vscode.window.showErrorMessage(`PenguPool: ${result.stderr || 'group failed'}`);
    }
  }

  private contains(root: SessionNode, id: string): boolean {
    return root.id === id || root.children.some((child) => this.contains(child, id));
  }
}
