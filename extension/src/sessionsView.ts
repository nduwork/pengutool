import * as vscode from 'vscode';
import { Harness, SessionNode, Snapshot } from './serveClient';
import { SessionsProvider } from './sessionsTree';
import { SESSION_STATES, SESSION_STATE_CSS } from './sessionState';
import { bothHarnesses, findNode, splitByHarness } from './harness';

type VisibilityEvent = { visible: boolean };

/** Webview-backed Sessions list. VS Code's native TreeView discards context-menu events whose
 * target is empty space, so the view owns its background menu while preserving tree interactions.
 * One instance per harness view: while both harnesses run each lists only its own harness; otherwise
 * the Claude Code (main) view lists everything and the pi view is hidden. */
export class SessionsView implements vscode.WebviewViewProvider, vscode.Disposable {
  /** One selection spans both views, so commands act on it whichever view it was made in. */
  private static selectedId = '';
  private static readonly views = new Set<SessionsView>();

  private readonly _onDidChangeVisibility = new vscode.EventEmitter<VisibilityEvent>();
  readonly onDidChangeVisibility = this._onDidChangeVisibility.event;

  private view?: vscode.WebviewView;
  private snapshot?: Snapshot;
  private ready = false;
  private readonly disposables: vscode.Disposable[] = [];

  constructor(private readonly provider: SessionsProvider, private readonly harness: Harness = 'cc') {
    SessionsView.views.add(this);
  }

  get visible(): boolean { return this.view?.visible ?? false; }

  get selection(): readonly SessionNode[] {
    const node = SessionsView.selectedId ? this.provider.find(SessionsView.selectedId) : undefined;
    return node ? [node] : [];
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    this.ready = false;
    view.webview.options = { enableScripts: true };
    view.webview.html = sessionsHtml();
    this.disposables.push(
      view.onDidChangeVisibility(() => this._onDidChangeVisibility.fire({ visible: view.visible })),
      view.webview.onDidReceiveMessage((message) => { void this.receive(message); }),
    );
    this._onDidChangeVisibility.fire({ visible: view.visible });
  }

  update(snapshot: Snapshot): void {
    this.snapshot = snapshot;
    if (SessionsView.selectedId && !this.provider.find(SessionsView.selectedId)) { SessionsView.selectedId = ''; }
    this.postState();
  }

  /** Sessions this view lists: its own harness while both run, else everything (main view only). */
  shown(): Snapshot | undefined {
    const snap = this.snapshot;
    if (!snap || (this.harness === 'cc' && !bothHarnesses(snap.roots))) { return snap; }
    return splitByHarness(snap, this.harness);
  }

  /** Selects `node` in every view; only the view listing it focuses. */
  async reveal(node: SessionNode, options?: { select?: boolean; focus?: boolean; expand?: boolean }): Promise<void> {
    if (options?.select !== false) { SessionsView.selectedId = node.id; }
    await Promise.all([...SessionsView.views].map(async (view) => {
      const focus = Boolean(options?.focus) && Boolean(findNode(view.shown()?.roots ?? [], node.id));
      await view.postState(focus);
      if (focus) { view.view?.show?.(true); }
    }));
  }

  private async receive(message: any): Promise<void> {
    if (message?.type === 'ready') {
      this.ready = true;
      await this.postState();
      return;
    }
    if (message?.type === 'select' && typeof message.id === 'string') {
      SessionsView.selectedId = this.provider.find(message.id) ? message.id : '';
      await Promise.all([...SessionsView.views].map((view) => view.postState()));
      return;
    }
    if (message?.type === 'group' && typeof message.source === 'string') {
      await this.provider.group(message.source, typeof message.target === 'string' ? message.target : '');
      return;
    }
    if (message?.type !== 'command' || typeof message.command !== 'string') { return; }
    const global = new Set(['pengupool.new', 'pengupool.add']);
    const perSession = new Set([
      'pengupool.switch', 'pengupool.group', 'pengupool.rename', 'pengupool.describe',
      'pengupool.compact', 'pengupool.close',
    ]);
    if (global.has(message.command)) {
      await vscode.commands.executeCommand(message.command);
      return;
    }
    if (!perSession.has(message.command) || typeof message.id !== 'string') { return; }
    const node = this.provider.find(message.id);
    if (!node) { return; }
    SessionsView.selectedId = node.id;
    await vscode.commands.executeCommand(message.command, message.command === 'pengupool.switch' ? node.id : node);
  }

  private async postState(focus = false): Promise<void> {
    const snapshot = this.shown();
    if (!this.ready || !snapshot || !this.view) { return; }
    await this.view.webview.postMessage({ type: 'snapshot', snapshot, selectedId: SessionsView.selectedId, focus });
  }

  dispose(): void {
    SessionsView.views.delete(this);
    this.disposables.splice(0).forEach((item) => item.dispose());
    this._onDidChangeVisibility.dispose();
  }
}

export function sessionsHtml(): string {
  const nonce = Math.random().toString(36).slice(2);
  const csp = `default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';`;
  return `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<style>
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; overflow:hidden; color:var(--vscode-foreground);
    background:var(--vscode-sideBar-background); font:var(--vscode-font-size)/1.35 var(--vscode-font-family); }
  #tree { height:100%; overflow:auto; padding:3px 0 24px; outline:none; }
  .row { height:24px; display:flex; align-items:center; gap:4px; padding-right:8px; cursor:default;
    user-select:none; white-space:nowrap; }
  .row:hover { background:var(--vscode-list-hoverBackground); }
  .row.selected { color:var(--vscode-list-activeSelectionForeground);
    background:var(--vscode-list-activeSelectionBackground); }
  #tree:not(:focus-within) .row.selected { color:var(--vscode-list-inactiveSelectionForeground);
    background:var(--vscode-list-inactiveSelectionBackground); }
  .twist { width:16px; height:20px; padding:0; border:0; color:inherit; background:none; font:inherit; }
  .twist.empty { visibility:hidden; }
  .glyph { width:12px; text-align:center; color:var(--state-color); }
  .name { overflow:hidden; text-overflow:ellipsis; }
  .desc { margin-left:auto; color:var(--vscode-descriptionForeground); overflow:hidden; text-overflow:ellipsis; }
  .children.collapsed { display:none; }
  #empty { padding:8px 20px; color:var(--vscode-descriptionForeground); }
  #menu { position:fixed; z-index:10; min-width:210px; padding:4px 0; display:none;
    color:var(--vscode-menu-foreground); background:var(--vscode-menu-background);
    border:1px solid var(--vscode-menu-border, var(--vscode-widget-border));
    box-shadow:0 2px 8px var(--vscode-widget-shadow); }
  #menu button { display:block; width:100%; height:24px; padding:2px 24px; text-align:left; border:0;
    color:inherit; background:none; font:inherit; }
  #menu button:hover, #menu button:focus { color:var(--vscode-menu-selectionForeground);
    background:var(--vscode-menu-selectionBackground); outline:none; }
  #menu hr { margin:4px 0; border:0; border-top:1px solid var(--vscode-menu-separatorBackground); }
  ${SESSION_STATE_CSS}
</style></head><body>
<div id="tree" role="tree" aria-label="PenguPool sessions" tabindex="0"></div>
<div id="menu" role="menu"></div>
<script nonce="${nonce}">
  const vscode = acquireVsCodeApi();
  const tree = document.getElementById('tree');
  const menu = document.getElementById('menu');
  const states = ${JSON.stringify(SESSION_STATES)};
  let selected = '', dragged = '', menuId = '';
  const collapsed = new Set();

  const command = (name, id=menuId) => vscode.postMessage({type:'command', command:name, id});
  function rows(){ return [...tree.querySelectorAll('.row')]; }
  function select(id, notify=true){
    selected=id||''; rows().forEach(row => row.classList.toggle('selected', row.dataset.id===selected));
    if(notify) vscode.postMessage({type:'select', id:selected});
  }
  function hideMenu(){ menu.style.display='none'; menu.innerHTML=''; }
  function addMenuItem(label, cmd){
    const button=document.createElement('button'); button.type='button'; button.role='menuitem';
    button.textContent=label; button.dataset.command=cmd; button.addEventListener('click',()=>{ hideMenu(); command(cmd); });
    menu.appendChild(button);
  }
  function showMenu(event, id){
    event.preventDefault(); event.stopPropagation(); menuId=id||''; if(id) select(id);
    hideMenu();
    addMenuItem('New Session', 'pengupool.new');
    addMenuItem('Add Previous Session…', 'pengupool.add');
    if(id){
      menu.appendChild(document.createElement('hr'));
      addMenuItem('Open / Focus Session', 'pengupool.switch');
      addMenuItem('Group Under…', 'pengupool.group');
      addMenuItem('Rename', 'pengupool.rename');
      addMenuItem('Describe Role…', 'pengupool.describe');
      addMenuItem('Compact (/compact)', 'pengupool.compact');
      menu.appendChild(document.createElement('hr'));
      addMenuItem('Close', 'pengupool.close');
    }
    menu.style.display='block';
    const box=menu.getBoundingClientRect();
    menu.style.left=Math.max(2,Math.min(event.clientX,innerWidth-box.width-2))+'px';
    menu.style.top=Math.max(2,Math.min(event.clientY,innerHeight-box.height-2))+'px';
    menu.querySelector('button')?.focus();
  }

  function renderNode(node, depth){
    const wrap=document.createElement('div');
    const row=document.createElement('div'); row.className='row state-'+node.state; row.dataset.id=node.id;
    row.setAttribute('role','treeitem'); row.setAttribute('aria-level',String(depth+1)); row.draggable=true;
    row.style.paddingLeft=(depth*14+3)+'px';
    const twist=document.createElement('button'); twist.className='twist'+(node.children.length?'':' empty');
    twist.type='button'; twist.textContent='⌄'; twist.tabIndex=-1; twist.setAttribute('aria-label','Collapse '+node.name);
    const state=states[node.state]||{symbol:'·',label:node.state};
    const glyph=document.createElement('span'); glyph.className='glyph'; glyph.textContent=state.symbol;
    const name=document.createElement('span'); name.className='name'; name.textContent=node.name;
    const desc=document.createElement('span'); desc.className='desc';
    desc.textContent=(node.harness==='pi'?'pi · ':'')+(node.repo||'')+(node.ctx_pct!=null?' · '+node.ctx_pct+'% ctx':'');
    row.title=[node.name,node.summary||'role not set',state.label,node.cwd,node.status].filter(Boolean).join('\\n');
    row.setAttribute('aria-label',node.name+', '+state.label);
    row.append(twist,glyph,name,desc); wrap.appendChild(row);
    const children=document.createElement('div'); children.className='children';
    if(collapsed.has(node.id)){ children.classList.add('collapsed'); twist.textContent='›'; }
    node.children.forEach(child=>children.appendChild(renderNode(child,depth+1))); wrap.appendChild(children);
    twist.addEventListener('click',event=>{ event.stopPropagation(); const closed=children.classList.toggle('collapsed');
      closed?collapsed.add(node.id):collapsed.delete(node.id);
      twist.textContent=closed?'›':'⌄'; row.setAttribute('aria-expanded',String(!closed)); });
    row.addEventListener('click',()=>command('pengupool.switch',node.id));
    row.addEventListener('contextmenu',event=>showMenu(event,node.id));
    row.addEventListener('dragstart',event=>{ dragged=node.id; event.dataTransfer?.setData('text/plain',node.id); });
    row.addEventListener('dragover',event=>event.preventDefault());
    row.addEventListener('drop',event=>{ event.preventDefault(); event.stopPropagation();
      if(dragged&&dragged!==node.id) vscode.postMessage({type:'group',source:dragged,target:node.id}); dragged=''; });
    return wrap;
  }
  function render(snapshot){
    const scroll=tree.scrollTop;
    tree.innerHTML='';
    if(!snapshot.roots.length){ const empty=document.createElement('div'); empty.id='empty';
      empty.textContent='No sessions'; tree.appendChild(empty); }
    snapshot.roots.forEach(node=>tree.appendChild(renderNode(node,0)));
    if(!rows().some(row=>row.dataset.id===selected)) selected='';   // selected in the other harness's view
    select(selected,false);
    tree.scrollTop=scroll;
  }

  tree.addEventListener('contextmenu',event=>{ if(!event.target.closest('.row')) showMenu(event, null); });
  tree.addEventListener('dragover',event=>event.preventDefault());
  tree.addEventListener('drop',event=>{ if(event.target.closest('.row')) return; event.preventDefault();
    if(dragged) vscode.postMessage({type:'group',source:dragged,target:''}); dragged=''; });
  tree.addEventListener('keydown',event=>{
    if(menu.style.display==='block') return;
    const list=rows(); let index=list.findIndex(row=>row.dataset.id===selected);
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){
      event.preventDefault(); index=event.key==='ArrowDown'?Math.min(list.length-1,index+1):Math.max(0,index<0?0:index-1);
      if(list[index]){ select(list[index].dataset.id); list[index].scrollIntoView({block:'nearest'}); } return;
    }
    if(event.key==='Enter'&&selected){ event.preventDefault(); command('pengupool.switch',selected); return; }
    const shortcuts={n:'pengupool.new',a:'pengupool.add',g:'pengupool.group',r:'pengupool.rename',d:'pengupool.describe',x:'pengupool.close',c:'pengupool.compact'};
    const cmd=shortcuts[event.key]; if(cmd&&(!['g','r','x','c'].includes(event.key)||selected)){
      event.preventDefault(); command(cmd,selected); }
  });
  document.addEventListener('pointerdown',event=>{ if(!menu.contains(event.target)) hideMenu(); });
  document.addEventListener('keydown',event=>{ if(event.key==='Escape') hideMenu(); });
  window.addEventListener('message',event=>{ if(event.data?.type!=='snapshot') return;
    selected=event.data.selectedId||''; render(event.data.snapshot); if(event.data.focus) tree.focus(); });
  vscode.postMessage({type:'ready'});
</script></body></html>`;
}
