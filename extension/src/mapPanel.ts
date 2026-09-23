import * as vscode from 'vscode';
import { Snapshot } from './serveClient';
import { CTX_LEVEL_CSS, CTX_LEVEL_JS, SESSION_STATES, SESSION_STATE_CSS } from './sessionState';
import { HarnessTabs, HARNESS_TABS_CSS, HARNESS_TABS_HTML, HARNESS_TABS_JS } from './harness';

/**
 * Live map as an editor-area webview (an editor tab, so it can be moved into a new/floating window or
 * tiled beside the code). Layout (dagre) is recomputed ONLY when `topo_hash` or a card's size changes;
 * other status/ctx ticks just restyle nodes in place, which keeps the 1 Hz refresh jump-free. Cards are
 * compact and grow to fit a workflow chain only when the session has one.
 */
export class MapPanel {
  private static current?: MapPanel;
  private readonly panel: vscode.WebviewPanel;
  private last?: Snapshot;
  private selectedId = '';
  private readonly tabs = new HarnessTabs();

  static toggle(ctx: vscode.ExtensionContext, last?: Snapshot): void {
    if (MapPanel.current) { MapPanel.current.panel.dispose(); return; }
    MapPanel.show(ctx, last, vscode.ViewColumn.Beside);
  }

  /** Reveal the map without turning an already-open map into a close action. */
  static show(
    ctx: vscode.ExtensionContext,
    last?: Snapshot,
    column: vscode.ViewColumn = vscode.ViewColumn.Beside,
  ): MapPanel {
    if (MapPanel.current) {
      MapPanel.current.panel.reveal(column);
      if (last) { MapPanel.current.update(last); }
      return MapPanel.current;
    }
    const panel = vscode.window.createWebviewPanel(
      'pengupoolMap', 'PenguPool Map', column,
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [vscode.Uri.joinPath(ctx.extensionUri, 'media')] },
    );
    MapPanel.current = new MapPanel(panel, ctx);
    if (last) { MapPanel.current.update(last); }
    return MapPanel.current;
  }

  static showIfOpen(): MapPanel | undefined { return MapPanel.current; }

  private constructor(panel: vscode.WebviewPanel, ctx: vscode.ExtensionContext) {
    this.panel = panel;
    const dagre = panel.webview.asWebviewUri(vscode.Uri.joinPath(ctx.extensionUri, 'media', 'dagre.min.js'));
    this.panel.webview.html = html(panel.webview, dagre);
    this.panel.webview.onDidReceiveMessage((message) => {
      if (message?.type === 'ready') {
        this.render();
        if (this.selectedId) { void this.panel.webview.postMessage({ type: 'selection', id: this.selectedId }); }
      }
      if (message?.type === 'select' && typeof message.id === 'string') {
        void vscode.commands.executeCommand('pengupool.switch', message.id);
      }
      // Refresh: redraw from the last snapshot now, and restart `pengupool serve` so a fresh process
      // rebuilds the whole model from disk (sessions, transcripts, roles, chains) and sends it in full.
      if (message?.type === 'refresh') { this.render(); void vscode.commands.executeCommand('pengupool.refresh'); }
      if (message?.type === 'tab' && this.tabs.pick(message.harness)) { this.render(); }
    });
    this.panel.onDidDispose(() => { if (MapPanel.current === this) { MapPanel.current = undefined; } });
  }

  update(snap: Snapshot): void {
    this.last = snap;
    this.render();
  }

  select(id: string): void {
    this.selectedId = id;
    this.render();   // with no tab picked, the map follows the selected session's harness
    void this.panel.webview.postMessage({ type: 'selection', id });
  }

  private render(): void {
    if (!this.last) { return; }
    const { tabs, snapshot } = this.tabs.view(this.last, this.selectedId);
    void this.panel.webview.postMessage(tabs);
    void this.panel.webview.postMessage(snapshot);
  }
}

function html(webview: vscode.Webview, dagreUri: vscode.Uri): string {
  const nonce = Math.random().toString(36).slice(2);
  const csp = `default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}' ${webview.cspSource};`;
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<style>
  html,body { margin:0; height:100%; background: var(--vscode-editor-background); color: var(--vscode-foreground);
              font-family: var(--vscode-editor-font-family, monospace); font-size: 12px; }
  #wrap { position:absolute; inset:0; overflow:auto; }
  .edge { stroke: var(--vscode-descriptionForeground); stroke-opacity:.75; fill:none; stroke-width:2;
          stroke-linejoin:round; }  /* panel-border is a faint divider colour: edges vanished against it */
  .elabel { fill: var(--vscode-descriptionForeground); font-size:10px; }
  .box { stroke:var(--state-color, var(--vscode-panel-border)); stroke-width:1.5; rx:6;
         fill: var(--vscode-editorWidget-background); transition: stroke 200ms; }
  .selection { fill:none; stroke:transparent; stroke-width:2; rx:8; pointer-events:none; }
  .node.selected .selection { stroke:var(--vscode-focusBorder); }
  .node { cursor:pointer; }
  .node:hover .box, .node:focus .box { stroke-width:2.5; }
  .node:focus .selection { stroke:var(--vscode-focusBorder); }
  .node:focus { outline:none; }
  .state { color:var(--state-color); font-size:10px; line-height:14px; font-weight:600; }
  ${SESSION_STATE_CSS}
  ${CTX_LEVEL_CSS}
  .content { box-sizing:border-box; width:100%; height:100%; padding:6px 10px; overflow:hidden;
             display:flex; flex-direction:column; justify-content:center; }
  .nm { color:var(--vscode-editor-foreground, var(--vscode-foreground)); font-weight:600;
        line-height:14px; overflow-wrap:anywhere; display:-webkit-box; -webkit-box-orient:vertical;
        -webkit-line-clamp:2; overflow:hidden; }
  .meta { color:var(--vscode-descriptionForeground); font-size:10px; line-height:14px;
          white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .chain { font-size:10px; line-height:14px; overflow-wrap:anywhere; flex-shrink:0; }
  .probe { position:absolute; visibility:hidden; left:-10000px; top:0; height:auto; }
  .wprobe { position:absolute; visibility:hidden; left:-10000px; top:0; white-space:nowrap; display:inline-block; }
  #refresh { position:absolute; top:4px; right:8px; z-index:2; cursor:pointer; padding:2px 8px; border-radius:3px;
             border:1px solid var(--vscode-button-border, transparent); font:inherit; font-size:11px;
             background:var(--vscode-button-secondaryBackground); color:var(--vscode-button-secondaryForeground); }
  #refresh:hover { background:var(--vscode-button-secondaryHoverBackground); }
  #empty { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
           color: var(--vscode-descriptionForeground); }
  ${HARNESS_TABS_CSS}
  #tabs { position:absolute; top:0; left:0; right:0; z-index:1; height:28px; box-sizing:border-box; }
  body.tabbed #wrap, body.tabbed #empty { top:28px; }
</style></head><body>
${HARNESS_TABS_HTML}
<button id="refresh" title="Reload all sessions from disk and redraw the map">⟳ Refresh</button>
<div id="empty">no sessions</div>
<div id="wrap"><svg id="svg" width="100%" height="100%"><g id="scene"></g></svg></div>
<script nonce="${nonce}" src="${dagreUri}"></script>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const scene = document.getElementById('scene');
  const empty = document.getElementById('empty');
  const SVGNS = 'http://www.w3.org/2000/svg';
  const HTMLNS = 'http://www.w3.org/1999/xhtml';
  const states = ${JSON.stringify(SESSION_STATES)};
  ${CTX_LEVEL_JS}
  let topo = null, sizes = '', last = null;
  let selected = '';
  const nodeEls = new Map();

  function flat(roots){ const o=[]; const w=n=>{o.push(n); n.children.forEach(w);}; roots.forEach(w); return o; }
  function edges(roots, cross){
    const byName = {}; const list = [];
    flat(roots).forEach(n => byName[n.name]=n.id);
    const w = n => n.children.forEach(c => { list.push([n.id, c.id, c.label||'']); w(c); });
    roots.forEach(w);
    (cross||[]).forEach(([s,d,l]) => { if(byName[s]&&byName[d]) list.push([byName[s], byName[d], l||'']); });
    return list;
  }
  // Card size MEASURED from the real fonts (dagre needs sizes before layout): an offscreen card holds
  // the same content and styles. ctx% is sized as "100%" so a changing percentage never relayouts.
  const probe = hel('div',{class:'content probe'}); const wprobe = hel('span',{class:'wprobe'});
  document.body.appendChild(probe); document.body.appendChild(wprobe);
  function textW(text, cls){ wprobe.className='wprobe '+cls; wprobe.textContent=text; return wprobe.getBoundingClientRect().width; }
  function cardSize(n){
    const meta = (n.harness==='pi'?'pi · ':'') + (n.ctx_pct!=null?'100% · ':'') + (n.repo||'');
    const chain = n.status||'';
    const width = Math.ceil(Math.min(chain ? 320 : 220, Math.max(140, textW(n.name,'nm')+22, textW(meta,'meta')+22,
                                                               Math.min(textW(chain,'chain'), 240)+22)));
    probe.style.width = width+'px'; probe.innerHTML = '';
    [['state','● Active'], ['nm', n.name], ['meta', meta], ['chain', chain]].forEach(([cls, text]) => {
      if(!text) return; const d=hel('div',{class:cls}); d.textContent=text; probe.appendChild(d); });
    return { width, height: Math.ceil(probe.getBoundingClientRect().height) + 2 };
  }
  function sizeKey(roots){ return flat(roots).map(n => { const c=cardSize(n); return n.id+':'+c.width+'x'+c.height; }).join(); }
  function el(tag, attrs){ const e=document.createElementNS(SVGNS,tag); for(const k in attrs) e.setAttribute(k, attrs[k]); return e; }
  function hel(tag, attrs){ const e=document.createElementNS(HTMLNS,tag); for(const k in attrs) e.setAttribute(k, attrs[k]); return e; }

  function relayout(snap){
    scene.innerHTML=''; nodeEls.clear();
    const nodes = flat(snap.roots);
    empty.style.display = nodes.length ? 'none' : 'flex';
    if(!nodes.length) return;
    const g = new dagre.graphlib.Graph(); g.setGraph({rankdir:'TB', nodesep:24, ranksep:40, marginx:16, marginy:16});
    g.setDefaultEdgeLabel(()=>({}));
    nodes.forEach(n => {
      g.setNode(n.id, cardSize(n));
    });
    edges(snap.roots, snap.cross).forEach(([a,b,l]) => g.setEdge(a,b,{label:l}));
    dagre.layout(g);
    // edges first (under nodes)
    g.edges().forEach(e => {
      const pts = g.edge(e).points.map(p => p.x+','+p.y).join(' ');
      scene.appendChild(el('polyline', {class:'edge', points:pts}));
      const lbl = g.edge(e).label; if(lbl){ const m=g.edge(e).points[Math.floor(g.edge(e).points.length/2)];
        const t=el('text',{class:'elabel', x:m.x+4, y:m.y-2}); t.textContent=lbl.slice(0,40); scene.appendChild(t); }
    });
    nodes.forEach(n => {
      const nd=g.node(n.id); const gx=nd.x-nd.width/2, gy=nd.y-nd.height/2;
      const grp=el('g',{class:'node state-'+n.state, transform:'translate('+gx+','+gy+')', tabindex:'0', role:'button', 'aria-label':'Open '+n.name});
      const ring=el('rect',{class:'selection', x:-3, y:-3, width:nd.width+6, height:nd.height+6, rx:8});
      const rect=el('rect',{class:'box', width:nd.width, height:nd.height, rx:6});
      const title=el('title',{}); title.textContent=n.name;
      const body=el('foreignObject',{x:0, y:0, width:nd.width, height:nd.height});
      const content=hel('div',{class:'content'});
      const state=hel('div',{class:'state'}); const nm=hel('div',{class:'nm'}); const meta=hel('div',{class:'meta'});
      const harness=hel('span',{}), ctx=hel('span',{class:'ctx'}), repo=hel('span',{});  // ctx% is colored by level
      meta.appendChild(harness); meta.appendChild(ctx); meta.appendChild(repo);
      const chain=hel('div',{class:'chain'});
      content.appendChild(state); content.appendChild(nm); content.appendChild(meta); content.appendChild(chain); body.appendChild(content);
      const select=()=>vscode.postMessage({type:'select', id:n.id});
      grp.addEventListener('click', select);
      grp.addEventListener('keydown', ev=>{ if(ev.key==='Enter'||ev.key===' '){ ev.preventDefault(); select(); } });
      grp.appendChild(ring); grp.appendChild(rect); grp.appendChild(title); grp.appendChild(body); scene.appendChild(grp);
      nodeEls.set(n.id, {grp, title, state, nm, meta, harness, ctx, repo, chain});
    });
    const gr=g.graph(); document.getElementById('svg').setAttribute('viewBox', '0 0 '+(gr.width||100)+' '+(gr.height||100));
    document.getElementById('svg').setAttribute('width', (gr.width||100)); document.getElementById('svg').setAttribute('height', (gr.height||100));
  }

  function restyle(snap){
    flat(snap.roots).forEach(n => {
      const e = nodeEls.get(n.id); if(!e) return;
      const visual=states[n.state]||{symbol:'·',label:n.state};
      e.grp.setAttribute('class', 'node state-'+n.state+(selected===n.id?' selected':''));
      e.grp.setAttribute('aria-label','Open '+n.name+', '+visual.label);
      e.state.textContent=visual.symbol+' '+visual.label;
      e.nm.textContent = n.name;
      e.harness.textContent = n.harness==='pi' ? 'pi · ' : '';
      e.ctx.textContent = n.ctx_pct!=null ? n.ctx_pct+'%' : '';
      e.ctx.className = n.ctx_pct!=null ? 'ctx '+ctxLevel(n.ctx_pct) : 'ctx';
      e.repo.textContent = (n.ctx_pct!=null ? ' · ' : '') + (n.repo||'');
      e.chain.textContent = n.status || '';
      e.chain.style.display = n.status ? '' : 'none';   // no chain, no reserved space
      e.title.textContent = n.name + ' · '+visual.label + (n.repo ? ' · '+n.repo : '') + (n.status ? '\\n'+n.status : '');
    });
  }

  ${HARNESS_TABS_JS}
  window.addEventListener('message', ev => {
    if(ev.data?.type==='tabs') return;
    if(ev.data?.type==='selection'){
      selected=ev.data.id;
      nodeEls.forEach((e,id)=>e.grp.classList.toggle('selected', id===selected));
      return;
    }
    const snap = last = ev.data;
    const key = sizeKey(snap.roots);
    if(snap.topo_hash !== topo || key !== sizes || fresh){ fresh = false; topo = snap.topo_hash; sizes = key; relayout(snap); }
    restyle(snap);
  });
  // Refresh: forget the cached layout and redraw now, then reload everything from the backend and
  // lay the map out again from that fresh snapshot, even when its structure did not change.
  let fresh = false;
  function redraw(){ topo = null; sizes = ''; if(last){ relayout(last); restyle(last); } }
  document.getElementById('refresh').addEventListener('click', () => { fresh = true; redraw(); vscode.postMessage({type:'refresh'}); });
  document.fonts?.ready.then(redraw);   // sizes measured before the editor font loaded are wrong
  vscode.postMessage({type:'ready'});
</script></body></html>`;
}
