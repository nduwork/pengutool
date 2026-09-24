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
  /* @session and other cross-tree messages: dashed, in the Log's orange */
  .xedge { stroke:#f0883e; stroke-opacity:.85; fill:none; stroke-width:1.5; stroke-dasharray:4,4; }
  .elabel { fill: var(--vscode-descriptionForeground); font-size:9px; }
  .emask { fill: var(--vscode-editor-background); }   /* keeps the line from bleeding through a label */
  .node.lone .box { stroke-dasharray:4,4; }
  .eyebrow { fill: var(--vscode-descriptionForeground); font-size:9px; letter-spacing:.14em; }
  .hair { stroke: var(--vscode-panel-border); stroke-width:1; }            /* ungrouped while others are grouped */
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
  .chip { position:absolute; top:6px; right:8px; font-size:8px; line-height:11px; letter-spacing:.08em;
          padding:0 3px; border:1px solid currentColor; border-radius:2px; opacity:.7;
          color:var(--vscode-descriptionForeground); }
  .content { position:relative; box-sizing:border-box; width:100%; height:100%; padding:6px 10px; overflow:hidden;
             display:flex; flex-direction:column; justify-content:center; }
  .nm { color:var(--vscode-editor-foreground, var(--vscode-foreground)); font-weight:600;
        line-height:14px; overflow-wrap:anywhere; display:-webkit-box; -webkit-box-orient:vertical;
        -webkit-line-clamp:2; overflow:hidden; }
  .meta { color:var(--vscode-descriptionForeground); font-size:10px; line-height:14px;
          white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .chain { font-size:10px; line-height:14px; overflow-wrap:anywhere; flex-shrink:0; }
  .probe { position:absolute; visibility:hidden; left:-10000px; top:0; height:auto; }
  .wprobe { position:absolute; visibility:hidden; left:-10000px; top:0; white-space:nowrap; display:inline-block; }
  #tools { position:absolute; top:4px; right:8px; z-index:2; display:flex; gap:4px; }
  #tools button { cursor:pointer; padding:2px 8px; border-radius:3px;
             border:1px solid var(--vscode-button-border, transparent); font:inherit; font-size:11px;
             background:var(--vscode-button-secondaryBackground); color:var(--vscode-button-secondaryForeground); }
  #tools button:hover { background:var(--vscode-button-secondaryHoverBackground); }
  #empty { position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
           color: var(--vscode-descriptionForeground); }
  ${HARNESS_TABS_CSS}
  #tabs { position:absolute; top:0; left:0; right:0; z-index:1; height:28px; box-sizing:border-box; }
  body.tabbed #wrap, body.tabbed #empty { top:28px; }
  #legend { position:absolute; left:0; right:0; bottom:0; height:24px; box-sizing:border-box; padding:0 12px; display:none;
            gap:16px; align-items:center; font-size:10px; color:var(--vscode-descriptionForeground);
            border-top:1px solid var(--vscode-panel-border); background:var(--vscode-editor-background); }
  #legend svg { vertical-align:middle; margin-right:4px; }
  #wrap { bottom:24px; }
  #svg { margin-top:28px; }   /* below the toolbar */
</style></head><body>
${HARNESS_TABS_HTML}
<div id="tools">
  <button id="dir" title="Lay the map out top-down or left-right"></button>
  <button id="spacing" title="Space the cards compactly or roomily"></button>
  <button id="msgs" title="Show or hide @session message lines"></button>
  <button id="refresh" title="Reload all sessions from disk and redraw the map">⟳ Refresh</button>
</div>
<div id="empty">no sessions</div>
<div id="wrap"><svg id="svg" width="100%" height="100%"><defs>
  <marker id="xarrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6 z" fill="#f0883e"/></marker>
</defs><g id="scene"></g></svg></div>
<div id="legend">
  <span><svg width="18" height="8"><path d="M0,4 H18" class="edge"/></svg>parent → child</span>
  <span><svg width="18" height="8"><path d="M0,4 H18" class="xedge"/></svg>@session message</span>
  <span><svg width="14" height="10"><rect x="1" y="1" width="12" height="8" rx="2" class="box" style="stroke:var(--vscode-descriptionForeground); stroke-dasharray:3,2"/></svg>ungrouped</span>
</div>
<script nonce="${nonce}" src="${dagreUri}"></script>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const scene = document.getElementById('scene');
  const empty = document.getElementById('empty');
  const legend = document.getElementById('legend');
  const SVGNS = 'http://www.w3.org/2000/svg';
  const HTMLNS = 'http://www.w3.org/1999/xhtml';
  const states = ${JSON.stringify(SESSION_STATES)};
  ${CTX_LEVEL_JS}
  let topo = null, sizes = '', last = null;
  let selected = '';
  const nodeEls = new Map();

  function flat(roots){ const o=[]; const w=n=>{o.push(n); n.children.forEach(w);}; roots.forEach(w); return o; }
  // Tree edges parent → child, and cross edges between branches. A cross edge that runs along a tree
  // edge (a child's reply to its parent) is left out: it would sit on top of that edge, and the Log has it.
  function edges(roots, cross){
    const byName = {}; const tree = [], xs = [], along = new Set();
    flat(roots).forEach(n => byName[n.name]=n.id);
    const w = n => n.children.forEach(c => { tree.push([n.id, c.id, c.label||'']); along.add(n.id+'>'+c.id); along.add(c.id+'>'+n.id); w(c); });
    roots.forEach(w);
    (cross||[]).forEach(([s,d,l]) => { const a=byName[s], b=byName[d];
      if(a && b && !along.has(a+'>'+b)) xs.push([a, b, l||'']); });
    return { tree, cross: xs };
  }
  // A path through right-angle points with each bend rounded (r=6, less where a segment is short).
  function rounded(pts){
    pts = pts.filter((p,i) => !i || p[0]!==pts[i-1][0] || p[1]!==pts[i-1][1]);
    const toward = (a,b,r) => { const d=Math.hypot(b[0]-a[0], b[1]-a[1])||1; return [a[0]+(b[0]-a[0])*r/d, a[1]+(b[1]-a[1])*r/d]; };
    let d = 'M'+pts[0][0]+','+pts[0][1];
    for(let i=1; i<pts.length-1; i++){
      const [p,c,n] = [pts[i-1], pts[i], pts[i+1]];
      const r = Math.min(6, Math.hypot(c[0]-p[0], c[1]-p[1])/2, Math.hypot(n[0]-c[0], n[1]-c[1])/2);
      const a = toward(c,p,r), b = toward(c,n,r);
      d += ' L'+a[0]+','+a[1]+' Q'+c[0]+','+c[1]+' '+b[0]+','+b[1];
    }
    const e = pts[pts.length-1]; return d+' L'+e[0]+','+e[1];
  }
  // Map options, kept per panel: direction, spacing, and whether @session lines are drawn.
  const LAYOUTS = { TB:'↓ Top-down', LR:'→ Left-right' }, SPACINGS = { compact:'Compact', roomy:'Roomy' };
  const opts = Object.assign({ dir:'TB', spacing:'compact', msgs:true }, vscode.getState?.()?.opts);
  function spacing(){ const roomy = opts.spacing==='roomy';
    return opts.dir==='TB' ? { nodesep: roomy ? 40 : 24, ranksep: roomy ? 88 : 56 }
                           : { nodesep: roomy ? 28 : 16, ranksep: roomy ? 200 : 136 }; }  // LR: labels run along
  // Only the tree goes to dagre, so an @session line never bends the tree. Lines are routed on the laid-out
  // cards: a tree edge leaves the parent, turns on a bus 16px out (shared by the siblings) and runs into the
  // child. An @session line turns in the gaps between ranks and crosses ranks only in a lane no card
  // touches, attaching 16px off the cards' centre lines, on the side away from the labels and the tree's strokes.
  function geometry(boxes, lone, laneX){
    const H = opts.dir==='TB', ids = Object.keys(boxes);
    const cards = ids.map(id => { const n=boxes[id];
      return { id, main:H?n.y:n.x, side:H?n.x:n.y, hm:(H?n.height:n.width)/2, hs:(H?n.width:n.height)/2 }; });
    const ranks = {}; cards.filter(c => !lone.has(c.id)).forEach(c => { const k=Math.round(c.main); ranks[k]=Math.max(ranks[k]||0, c.hm); });
    const centres = Object.keys(ranks).map(Number).sort((a,b)=>a-b);
    const pt = (m, s) => H ? [s, m] : [m, s];
    const at = id => cards[ids.indexOf(id)];
    // the gap on the sgn side of the rank at centre c (past the last rank: 12px beyond it)
    const gap = (c, sgn) => { const k=Math.round(c), next=centres[centres.indexOf(k)+sgn];
      return next===undefined ? k+sgn*(ranks[k]+12) : (k+sgn*ranks[k] + next-sgn*ranks[next])/2; };
    const clear = (s, m1, m2) => cards.every(c => Math.abs(c.side-s) > c.hs+4 || Math.max(m1,m2) < c.main-c.hm || Math.min(m1,m2) > c.main+c.hm);
    return { pt, at, gap, clear, cards, H, box: id => boxes[id], lone, laneX };
  }
  function treeRoute(G, a, b){
    const A=G.at(a), B=G.at(b), bus=A.main+A.hm+16;
    return [G.pt(A.main+A.hm, A.side), G.pt(bus, A.side), G.pt(bus, B.side), G.pt(B.main-B.hm, B.side)];
  }
  // To or from the ungrouped column: leave the tree card into the gap past its rank, run along the gap
  // (LR: then the top margin) to the lane left of the column, and into the ungrouped card's left edge.
  // k spreads several such lines apart so none shares a stroke.
  const lanes6 = k => (k%3 - 1)*6;   // ponytail: 3 offsets; a 4th line in one gap may share a stroke
  function loneRoute(G, a, b, k){
    if(G.lone.has(a) && !G.lone.has(b)) return loneRoute(G, b, a, k).reverse();
    const L = G.box(b), lane = G.laneX - 5*(k%3), ly = L.y + Math.min(5*(k%3), L.height/2-6), end = [L.x-L.width/2, ly];
    if(G.lone.has(a)){ const A=G.box(a); return [[A.x-A.width/2, A.y], [lane, A.y], [lane, ly], end]; }
    const A = G.at(a), sa = A.side + (G.H ? -1 : 1)*Math.min(16, A.hs-8), gp = G.gap(A.main, 1) + lanes6(k);
    const start = G.pt(A.main+A.hm, sa);
    return G.H ? [start, G.pt(gp, sa), [lane, gp], [lane, ly], end]
               : [start, G.pt(gp, sa), [gp, 8+5*(k%3)], [lane, 8+5*(k%3)], [lane, ly], end];
  }
  function crossRoute(G, a, b, k){
    if(G.lone.has(a) || G.lone.has(b)) return loneRoute(G, a, b, k);
    const A=G.at(a), B=G.at(b);
    const off = (opts.dir==='TB' ? -1 : 1) * 16;   // the side away from the labels: right of a TB drop, above an LR line
    const sa = A.side + Math.sign(off)*Math.min(16, A.hs-8), sb = B.side + Math.sign(off)*Math.min(16, B.hs-8);
    const sgn = B.main > A.main+1 ? 1 : B.main < A.main-1 ? -1 : 1;           // same rank: go round below
    const ya = G.gap(A.main, sgn) + lanes6(k), yb = Math.abs(B.main-A.main) <= 1 ? ya : G.gap(B.main, -sgn) + lanes6(k);
    const start = G.pt(A.main+sgn*A.hm, sa), end = G.pt(Math.abs(B.main-A.main) <= 1 ? B.main+sgn*B.hm : B.main-sgn*B.hm, sb);
    if(Math.abs(ya-yb) < 1) return [start, G.pt(ya, sa), G.pt(ya, sb), end];
    // a lane from ya to yb that no card touches: prefer one near the two ends; a lane beside any card works
    const lanes = [sa, sb, ...G.cards.flatMap(c => [c.side-c.hs-10, c.side+c.hs+10])]
      .filter(s => G.clear(s, ya, yb)).sort((p,q) => Math.abs(p-sa)+Math.abs(p-sb) - Math.abs(q-sa)-Math.abs(q-sb));
    const lane = lanes.length ? lanes[0] : sa;
    return [start, G.pt(ya, sa), G.pt(ya, lane), G.pt(yb, lane), G.pt(yb, sb), end];
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
    legend.style.display = nodes.length ? 'flex' : 'none';
    if(!nodes.length) return;
    // Sessions outside every tree (while some tree exists) skip dagre: they stack in their own column to
    // the right, left edges aligned, 16px apart whatever their heights, under an UNGROUPED heading and a hairline.
    const lead = new Set(snap.roots.filter(r => r.children.length).map(r => r.id));
    const lone = new Set(lead.size ? snap.roots.filter(r => !r.children.length).map(r => r.id) : []);
    const g = new dagre.graphlib.Graph(); g.setGraph({rankdir:opts.dir, ...spacing(), marginx:24, marginy:24});
    g.setDefaultEdgeLabel(()=>({}));
    nodes.forEach(n => { if(!lone.has(n.id)) g.setNode(n.id, cardSize(n)); });
    const es = edges(snap.roots, opts.msgs ? snap.cross : []);
    es.tree.forEach(([a,b]) => g.setEdge(a,b));
    dagre.layout(g);
    const gr = g.graph(), boxes = {};
    g.nodes().forEach(id => { boxes[id] = g.node(id); });
    const loners = nodes.filter(n => lone.has(n.id)), colX = (gr.width||0) + 16;
    const colW = Math.max(0, ...loners.map(n => cardSize(n).width));
    let cy = 24 + 20, width = gr.width||100, height = gr.height||100;
    if(loners.length){
      scene.appendChild(el('line', {class:'hair', x1:colX-24, y1:12, x2:colX-24, y2:Math.max(height, 44 + loners.reduce((h,n) => h+cardSize(n).height+16, 0)) - 12}));
      const head = el('text', {class:'eyebrow', x:colX, y:24+8}); head.textContent = 'UNGROUPED'; scene.appendChild(head);
      loners.forEach(n => { const c = cardSize(n);
        boxes[n.id] = { width: c.width, height: c.height, x: colX + c.width/2, y: cy + c.height/2 }; cy += c.height + 16; });  // left-aligned: a card grows to the right; the gap stays 16px
      width = colX + colW + 24; height = Math.max(height, cy + 8);
    }
    const G = geometry(boxes, lone, colX - 8);
    // edges first (under nodes)
    es.cross.forEach(([a,b], k) => scene.appendChild(el('path', {class:'xedge', d:rounded(crossRoute(G, a, b, k)), 'marker-end':'url(#xarrow)'})));
    const routes = es.tree.map(([a,b,l]) => { const pts=treeRoute(G, a, b);
      scene.appendChild(el('path', {class:'edge', d:rounded(pts)})); return [pts, l, boxes[b]]; });
    // labels over the edges, each on a mask 6px off the last segment into the child, cut to the room there
    routes.forEach(([pts, l, c]) => { if(!pts || !l) return;
      const [j, end] = pts.slice(-2), H = opts.dir==='TB';
      const room = H ? c.width/2 + 60 : Math.abs(end[0]-j[0]) - 12;
      let text = l; while(text.length > 1 && textW(text,'elabel') > room) text = text.slice(0,-2)+'…';
      if(text.length < 3) return;
      const w = Math.ceil(textW(text,'elabel'));
      const x = H ? j[0]+6 : Math.min(j[0],end[0])+6, y = H ? end[1]-18 : j[1]-18;   // 6px off the child (TB) or the line (LR): the gap's middle stays free for @ lines
      scene.appendChild(el('rect', {class:'emask', x, y, width:w+4, height:12, rx:2}));
      const t=el('text',{class:'elabel', x:x+2, y:y+9}); t.textContent=text; scene.appendChild(t);
      const tip=el('title',{}); tip.textContent=l; t.appendChild(tip); });
    nodes.forEach(n => {
      const nd=boxes[n.id]; const gx=nd.x-nd.width/2, gy=nd.y-nd.height/2;
      const grp=el('g',{class:'node state-'+n.state+(lone.has(n.id)?' lone':''), transform:'translate('+gx+','+gy+')', tabindex:'0', role:'button', 'aria-label':'Open '+n.name});
      const ring=el('rect',{class:'selection', x:-3, y:-3, width:nd.width+6, height:nd.height+6, rx:8});
      const rect=el('rect',{class:'box', width:nd.width, height:nd.height, rx:6});
      const title=el('title',{}); title.textContent=n.name;
      const body=el('foreignObject',{x:0, y:0, width:nd.width, height:nd.height});
      const content=hel('div',{class:'content'});
      const state=hel('div',{class:'state'}); const nm=hel('div',{class:'nm'}); const meta=hel('div',{class:'meta'});
      const harness=hel('span',{}), ctx=hel('span',{class:'ctx'}), repo=hel('span',{});  // ctx% is colored by level
      meta.appendChild(harness); meta.appendChild(ctx); meta.appendChild(repo);
      const chain=hel('div',{class:'chain'});
      if(lead.has(n.id)){ const chip=hel('span',{class:'chip'}); chip.textContent='LEAD'; content.appendChild(chip); }
      content.appendChild(state); content.appendChild(nm); content.appendChild(meta); content.appendChild(chain); body.appendChild(content);
      const select=()=>vscode.postMessage({type:'select', id:n.id});
      grp.addEventListener('click', select);
      grp.addEventListener('keydown', ev=>{ if(ev.key==='Enter'||ev.key===' '){ ev.preventDefault(); select(); } });
      grp.appendChild(ring); grp.appendChild(rect); grp.appendChild(title); grp.appendChild(body); scene.appendChild(grp);
      nodeEls.set(n.id, {grp, title, state, nm, meta, harness, ctx, repo, chain, lone: lone.has(n.id)});
    });
    document.getElementById('svg').setAttribute('viewBox', '0 0 '+width+' '+height);
    document.getElementById('svg').setAttribute('width', width); document.getElementById('svg').setAttribute('height', height);
  }

  function restyle(snap){
    flat(snap.roots).forEach(n => {
      const e = nodeEls.get(n.id); if(!e) return;
      const visual=states[n.state]||{symbol:'·',label:n.state};
      e.grp.setAttribute('class', 'node state-'+n.state+(e.lone?' lone':'')+(selected===n.id?' selected':''));
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
  function showOpts(){
    document.getElementById('dir').textContent = LAYOUTS[opts.dir];
    document.getElementById('spacing').textContent = SPACINGS[opts.spacing];
    document.getElementById('msgs').textContent = opts.msgs ? '@ lines: on' : '@ lines: off';
  }
  function setOpt(k, v){ opts[k] = v; vscode.setState?.({ opts }); showOpts(); redraw(); }
  document.getElementById('dir').addEventListener('click', () => setOpt('dir', opts.dir==='TB' ? 'LR' : 'TB'));
  document.getElementById('spacing').addEventListener('click', () => setOpt('spacing', opts.spacing==='compact' ? 'roomy' : 'compact'));
  document.getElementById('msgs').addEventListener('click', () => setOpt('msgs', !opts.msgs));
  showOpts();
  document.getElementById('refresh').addEventListener('click', () => { fresh = true; redraw(); vscode.postMessage({type:'refresh'}); });
  document.fonts?.ready.then(redraw);   // sizes measured before the editor font loaded are wrong
  vscode.postMessage({type:'ready'});
</script></body></html>`;
}
