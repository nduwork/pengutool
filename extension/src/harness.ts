import * as vscode from 'vscode';
import type { Harness, SessionNode, Snapshot } from './serveClient';

export const harnessOf = (node: SessionNode): Harness => node.harness ?? 'cc';

/** Harnesses with at least one running session. A tree never mixes harnesses, so roots suffice. */
export function harnessesIn(roots: readonly SessionNode[]): Set<Harness> {
  return new Set(roots.map(harnessOf));
}

export function bothHarnesses(roots: readonly SessionNode[]): boolean {
  const running = harnessesIn(roots);
  return running.has('cc') && running.has('pi');
}

export function findNode(roots: readonly SessionNode[], id: string): SessionNode | undefined {
  for (const node of roots) {
    const hit = node.id === id ? node : findNode(node.children, id);
    if (hit) { return hit; }
  }
  return undefined;
}

/** A copy of `snap` with only `harness`'s trees, plus the cross links and messages whose endpoints (by
 *  name) are both that harness's sessions. `topo_hash` gains the harness, so switching harness
 *  relayouts the map while a status-only tick still restyles in place. */
export function splitByHarness(snap: Snapshot, harness: Harness): Snapshot {
  const roots = snap.roots.filter((root) => harnessOf(root) === harness);
  const names = new Set<string>();
  const walk = (node: SessionNode) => { names.add(node.name); node.children.forEach(walk); };
  roots.forEach(walk);
  return {
    ...snap,
    roots,
    topo_hash: `${snap.topo_hash}:${harness}`,
    cross: snap.cross.filter(([src, dst]) => names.has(src) && names.has(dst)),
    msgs: snap.msgs.filter(([, src, dst]) => names.has(src) && names.has(dst)),
  };
}

/** Keeps the `pengupool.bothHarnesses` context key (it shows the pi Sessions view) in step with snapshots. */
export function bothHarnessesContext(): (roots: readonly SessionNode[]) => void {
  let last: boolean | undefined;
  return (roots) => {
    const both = bothHarnesses(roots);
    if (both === last) { return; }
    last = both;
    void vscode.commands.executeCommand('setContext', 'pengupool.bothHarnesses', both);
  };
}

export interface TabsMessage { type: 'tabs'; shown: boolean; active: Harness; }

/** "Claude Code | pi" tabs for the map and log, shown only while both harnesses run. The active tab is
 *  the user's last pick, else the selected session's harness, else Claude Code. */
export class HarnessTabs {
  private picked?: Harness;

  pick(harness: unknown): boolean {
    if (harness !== 'cc' && harness !== 'pi') { return false; }
    this.picked = harness;
    return true;
  }

  view(snap: Snapshot, selectedId = ''): { tabs: TabsMessage; snapshot: Snapshot } {
    if (!bothHarnesses(snap.roots)) {
      this.picked = undefined;   // the pick lapses once only one harness is left
      return { tabs: { type: 'tabs', shown: false, active: 'cc' }, snapshot: snap };
    }
    const selected = selectedId ? findNode(snap.roots, selectedId) : undefined;
    const active = this.picked ?? (selected ? harnessOf(selected) : 'cc');
    return { tabs: { type: 'tabs', shown: true, active }, snapshot: splitByHarness(snap, active) };
  }
}

/** Webview half of HarnessTabs: styles, markup, and a script (needs `vscode`) that follows `tabs`
 *  messages and posts `{type:'tab', harness}` on click. `body.tabbed` is set while the strip shows. */
export const HARNESS_TABS_CSS = `
  #tabs { display:none; gap:14px; padding:0 8px; border-bottom:1px solid var(--vscode-panel-border);
          background:var(--vscode-editor-background); }
  body.tabbed #tabs { display:flex; }
  #tabs button { padding:5px 2px 3px; border:0; border-bottom:2px solid transparent; background:none;
                 color:var(--vscode-descriptionForeground); font:inherit; cursor:pointer; }
  #tabs button:hover { color:var(--vscode-foreground); }
  #tabs button[aria-selected="true"] { color:var(--vscode-foreground); border-bottom-color:var(--vscode-focusBorder); }
  #tabs button:focus-visible { outline:1px solid var(--vscode-focusBorder); }
`;

export const HARNESS_TABS_HTML = `<div id="tabs" role="tablist" aria-label="Harness">` +
  `<button type="button" role="tab" data-harness="cc" aria-selected="true">Claude Code</button>` +
  `<button type="button" role="tab" data-harness="pi" aria-selected="false">pi</button></div>`;

export const HARNESS_TABS_JS = `
  document.querySelectorAll('#tabs [data-harness]').forEach(tab => tab.addEventListener('click',
    () => vscode.postMessage({type:'tab', harness:tab.dataset.harness})));
  window.addEventListener('message', ev => {
    if(ev.data?.type!=='tabs') return;
    document.body.classList.toggle('tabbed', ev.data.shown);
    document.querySelectorAll('#tabs [data-harness]').forEach(tab =>
      tab.setAttribute('aria-selected', String(tab.dataset.harness===ev.data.active)));
  });
`;
