import * as vscode from 'vscode';
import { SessionNode, Snapshot } from './serveClient';
import { HarnessTabs, HARNESS_TABS_CSS, HARNESS_TABS_HTML, HARNESS_TABS_JS } from './harness';

/** Format an ISO timestamp in the machine's local timezone for the compact log display. */
export function localLogTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) { return value; }
  const two = (part: number) => String(part).padStart(2, '0');
  return `${two(date.getMonth() + 1)}/${two(date.getDate())}/${date.getFullYear()}-` +
    `${two(date.getHours())}:${two(date.getMinutes())}:${two(date.getSeconds())}`;
}

/** `[ts, src, dst, label, incoming, rel]` — `rel` is 'down' (direct parent→child, green), 'up'
 *  (direct child→parent, milky blue), 'tagged' (any other pair in the tree, which the routing guard
 *  allows only when the user @-tagged the session; orange) or '' (an end outside the tree). */
type Rel = 'down' | 'up' | 'tagged' | '';
type LogMsg = [string, string, string, string, boolean, Rel];

/** name -> set of ancestor names within the tree, for arrow-direction coloring. */
function buildAncestors(roots: SessionNode[]): Map<string, Set<string>> {
  const anc = new Map<string, Set<string>>();
  const walk = (n: SessionNode, acc: Set<string>) => {
    anc.set(n.name, new Set(acc));
    for (const c of n.children) { walk(c, new Set(acc).add(n.name)); }
  };
  roots.forEach((r) => walk(r, new Set()));
  return anc;
}

/** 'down' when src is dst's parent, 'up' when dst is src's parent (a reply), 'tagged' for any
 *  other pair in the tree, '' when either end is outside it. */
function relation(src: string, dst: string, anc: Map<string, Set<string>>): Rel {
  const s = anc.get(src), d = anc.get(dst);
  if (!s || !d) { return ''; }
  const parentOf = (a: Set<string>, b: Set<string>, name: string) => b.size === a.size + 1 && b.has(name) && [...a].every((x) => b.has(x));
  if (parentOf(s, d, src)) { return 'down'; }  // dst's ancestors = src's + src → src is dst's parent
  if (parentOf(d, s, dst)) { return 'up'; }    // src's ancestors = dst's + dst → dst is src's parent
  return 'tagged';
}

function messages(snap: Snapshot): LogMsg[] {
  const anc = buildAncestors(snap.roots);
  return snap.msgs.map(([ts, src, dst, label, incoming]) =>
    [localLogTime(ts), src, dst, label, incoming, relation(src, dst, anc)],
  );
}

/** Live message log as an editor-area webview (floatable/tileable like the map). Shows
 *  `src ⇢ dst: label` per cross-session message, newest last, appended live. Each row shows a
 *  one-line preview and **expands to the full message on click** (state kept across refreshes).
 *  The `src ⇢ dst` arrow is colored by edge: green parent→child, milky blue child→parent, orange for a
 *  message the user allowed by @-tagging a session. */
export class LogPanel {
  private static current?: LogPanel;
  private readonly panel: vscode.WebviewPanel;
  private last?: Snapshot;
  private selectedId = '';
  private readonly tabs = new HarnessTabs();

  static toggle(last?: Snapshot): void {
    if (LogPanel.current) { LogPanel.current.panel.dispose(); return; }
    LogPanel.show(last, vscode.ViewColumn.Beside);
  }

  /** Reveal the log without turning an already-open log into a close action. */
  static show(
    last?: Snapshot,
    column: vscode.ViewColumn = vscode.ViewColumn.Beside,
  ): LogPanel {
    if (LogPanel.current) {
      LogPanel.current.panel.reveal(column);
      if (last) { LogPanel.current.update(last); }
      return LogPanel.current;
    }
    const panel = vscode.window.createWebviewPanel(
      'pengupoolLog', 'PenguPool Log', column,
      { enableScripts: true, retainContextWhenHidden: true },
    );
    LogPanel.current = new LogPanel(panel);
    if (last) { LogPanel.current.update(last); }
    return LogPanel.current;
  }

  static showIfOpen(): LogPanel | undefined { return LogPanel.current; }

  private constructor(panel: vscode.WebviewPanel) {
    this.panel = panel;
    this.panel.webview.html = this.html();
    this.panel.webview.onDidReceiveMessage((message) => {
      if (message?.type === 'ready') { this.render(); }
      if (message?.type === 'tab' && this.tabs.pick(message.harness)) { this.render(); }
    });
    this.panel.onDidDispose(() => { if (LogPanel.current === this) { LogPanel.current = undefined; } });
  }

  update(snap: Snapshot): void {
    this.last = snap;
    this.render();
  }

  /** With no tab picked, the log follows the selected session's harness. */
  select(id: string): void {
    this.selectedId = id;
    this.render();
  }

  private render(): void {
    if (!this.last) { return; }
    const { tabs, snapshot } = this.tabs.view(this.last, this.selectedId);
    void this.panel.webview.postMessage(tabs);
    void this.panel.webview.postMessage(messages(snapshot));
  }

  private html(): string {
    const nonce = Math.random().toString(36).slice(2);
    const csp = `default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';`;
    return `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<style>
  body { margin:0; padding:8px; background: var(--vscode-editor-background); color: var(--vscode-foreground);
         font-family: var(--vscode-editor-font-family, monospace); font-size: 12px; }
  .row { padding:2px 0; border-bottom:1px solid var(--vscode-panel-border); cursor:pointer; display:flex; }
  .row:hover { background: var(--vscode-list-hoverBackground); }
  .ts { color: var(--vscode-descriptionForeground); margin-right:6px; flex:0 0 auto; white-space:nowrap; }
  .msg { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .row.open .msg { white-space:pre-wrap; overflow:visible; }
  .hint { flex:0 0 auto; color: var(--vscode-descriptionForeground); }
  .row.open .hint { color: var(--vscode-foreground); }
  .who { color:#3fb950; } .who.b { color:#9ecbff; } .who.o { color:#f0883e; } .who.n { color: var(--vscode-descriptionForeground); }
  .g { color:#3fb950; } .b { color:#9ecbff; } .o { color:#f0883e; }
  .legend { position:sticky; top:-8px; z-index:1; margin:-8px -8px 6px; padding:6px 8px;
            border-bottom:1px solid var(--vscode-panel-border); background:var(--vscode-editor-background);
            color:var(--vscode-descriptionForeground); }
  .legend span + span { margin-left:14px; }
  #empty { color: var(--vscode-descriptionForeground); }
  ${HARNESS_TABS_CSS}
  .legend #tabs { margin:-6px -8px 6px; }
</style></head><body>
<div class="legend">${HARNESS_TABS_HTML}<span class="g">parent → child</span><span class="b">child → parent</span><span class="o">@session (user-tagged)</span></div>
<div id="empty">no messages yet</div><div id="log"></div>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const log = document.getElementById('log'); const empty = document.getElementById('empty');
  const open = new Set();   // message keys kept expanded across live refreshes
  log.addEventListener('click', ev => {
    const row = ev.target.closest('.row'); if(!row) return;
    const k = row.dataset.k;
    if(open.has(k)){ open.delete(k); row.classList.remove('open'); }
    else { open.add(k); row.classList.add('open'); }
  });
  ${HARNESS_TABS_JS}
  window.addEventListener('message', ev => {
    if(ev.data?.type==='tabs') return;
    const msgs = ev.data || [];
    empty.style.display = msgs.length ? 'none' : 'block';
    const escA = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
    log.innerHTML = msgs.map(m => {
      const [ts, src, dst, label, incoming, rel] = m;
      const cls = rel === 'down' ? '' : rel === 'up' ? 'b' : rel === 'tagged' ? 'o' : 'n';
      const k = ts+'|'+src+'|'+dst+'|'+label;
      const isOpen = open.has(k);
      return '<div class="row'+(isOpen?' open':'')+'" data-k="'+escA(k)+'" title="Click to expand / collapse">'
        +'<span class="ts">'+esc(ts)+'</span>'
        +'<span class="msg"><span class="who '+cls+'">'+esc(src)+' ⇢ '+esc(dst)+'</span>: '+esc(label)+'</span>'
        +'<span class="hint">'+(isOpen?'▾':'▸')+'</span></div>';
    }).join('');
    window.scrollTo(0, document.body.scrollHeight);
  });
  vscode.postMessage({type:'ready'});
</script></body></html>`;
  }
}
