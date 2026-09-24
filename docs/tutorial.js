// Animated tutorial: plays docs/tutorial-script.md scene by scene on the editor mock (#stage).
// Every scene is a script of small steps over one state model; jumping to a scene fast-forwards the
// earlier ones. ?record=N plays scene N alone, for rendering docs/assets/tutorial/*.gif.
(() => {
  const tut = document.getElementById('tut');
  if (!tut) return;
  const stage = document.getElementById('stage');
  const $ = (s) => stage.querySelector(s);
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const RECORD = Number(new URLSearchParams(location.search).get('record')) || 0;
  const GLYPH = { active: '●', waiting: '◷', stale: '○', blocked: '?' };
  const LABEL = { active: 'Active', waiting: 'Waiting', stale: 'Stale', blocked: 'Approval' };
  const ctxLevel = (p) => (p < 30 ? 'ctx-low' : p < 60 ? 'ctx-mid' : 'ctx-high');  // as in the extension

  // ---- state + rendering ---------------------------------------------------------------------------
  let S;
  const fresh = () => ({ sessions: [], chain: '', logs: [], term: [], tab: 'zsh', sel: '' });
  const find = (id) => S.sessions.find((s) => s.id === id);
  const kids = (id) => S.sessions.filter((s) => s.parent === id && s.h === 'cc');
  const seen = new Set();

  function rowHTML(s, indent) {
    const isNew = !seen.has('row' + s.id); seen.add('row' + s.id);
    const meta = s.h === 'pi' ? esc(`pi · ${s.repo}`) : `${esc(s.repo)} · <span class="${ctxLevel(s.ctx)}">${s.ctx}% ctx</span>`;
    return `<div class="row st-${s.state}${S.sel === s.id ? ' sel' : ''}${indent ? ' in' : ''}${isNew ? ' fade' : ''}" data-id="${s.id}">`
      + `<span class="g">${GLYPH[s.state]}</span><b>${esc(s.name)}</b><em>${meta}</em></div>`;
  }
  function renderSide() {
    const cc = S.sessions.filter((s) => s.h === 'cc'), pi = S.sessions.filter((s) => s.h === 'pi');
    let h = '<div class="side-title">PENGUPOOL</div><div class="side-sec">⌄ CLAUDE SESSIONS</div>';
    if (!cc.length) h += '<div class="side-empty">No sessions yet · press <kbd>n</kbd></div>';
    for (const r of cc.filter((s) => !s.parent)) { h += rowHTML(r, false); for (const c of kids(r.id)) h += rowHTML(c, true); }
    h += '<div class="side-sec">⌄ PI SESSIONS</div>';
    for (const s of pi) h += rowHTML(s, false);
    h += '<div class="side-sec closed">› SHORTCUTS</div>';
    $('.ide-side').innerHTML = h;
  }
  function layout() {
    // as the real map: once a tree exists, sessions outside every tree stack in a column on the right
    const all = S.sessions.filter((s) => s.h === 'cc' && !s.parent), grouped = all.some((r) => kids(r.id).length);
    const loners = grouped ? all.filter((r) => !kids(r.id).length) : [], roots = all.filter((r) => !loners.includes(r));
    const area = loners.length ? 70 : 92;
    const units = roots.map((r) => Math.max(1, kids(r.id).length));
    const total = units.reduce((a, b) => a + b, 0) || 1;
    const w = Math.min(29, area / total);
    const pos = {}; let start = 0;
    loners.forEach((r) => { pos[r.id] = { x: 79, y: 0, w: 20, col: true }; });  // left edge; stacked in renderMap
    roots.forEach((r, i) => {
      const x0 = 4 + (start / total) * area, span = (units[i] / total) * area, ks = kids(r.id);
      pos[r.id] = { x: x0 + span / 2, y: ks.length ? 10 : 36, w: ks.length ? Math.min(60, w * 2.5) : w };  // room for the chain
      ks.forEach((k, j) => { pos[k.id] = { x: x0 + span * (j + 0.5) / ks.length, y: 62, w }; });
      start += units[i];
    });
    return pos;
  }
  function renderMap() {
    const pane = $('.cards'), pos = layout();
    const grouped = S.sessions.some((x) => x.h === 'cc' && x.parent);
    for (const s of S.sessions.filter((x) => x.h === 'cc')) {
      let el = pane.querySelector(`[data-id="${s.id}"]`);
      if (!el) { el = document.createElement('div'); el.dataset.id = s.id; el.className = 'card fade'; pane.appendChild(el); }
      const p = pos[s.id];
      const lead = !s.parent && kids(s.id).length, lone = grouped && !s.parent && !lead;  // as the real map marks them
      el.className = el.className.replace(/\bst-\w+|\bsel\b|\bblink\b|\blone\b/g, '').trim() + ` st-${s.state}` + (S.sel === s.id ? ' sel' : '') + (s.blink ? ' blink' : '') + (lone ? ' lone' : '');
      Object.assign(el.style, { left: p.x + '%', top: p.y + '%', width: p.w + '%' });
      el.classList.toggle('col', !!p.col);
      const chain = !s.parent && kids(s.id).length && S.chain ? `<div class="ch">${esc(S.chain)}</div>` : '';
      el.innerHTML = `<div class="st">${lead ? '<span class="chip">LEAD</span>' : ''}${GLYPH[s.state]} ${LABEL[s.state]}</div><div class="nm">${esc(s.name)}</div>`
        + `<div class="meta">${esc(s.repo)} · <span class="${ctxLevel(s.ctx)}">${s.ctx}%</span></div>${chain}`;
    }
    // tree lines; the last two messages light theirs as the real map does: green down, milky blue for a reply
    const lit = { lg: '', lb: '' }, recent = S.logs.slice(-2);
    let d = '';
    for (const s of S.sessions.filter((x) => x.h === 'cc' && x.parent)) {
      const a = pos[s.parent], b = pos[s.id]; if (!a || !b) continue;
      const seg = `M${a.x} ${a.y + 28} V51 H${b.x} V${b.y} `; d += seg;
      const m = recent.filter((l) => (l.src === s.parent && l.dst === s.id) || (l.src === s.id && l.dst === s.parent)).pop();
      if (m) lit[m.src === s.parent ? 'lg' : 'lb'] += seg;
    }
    $('.edges path').setAttribute('d', d);
    $('.edges path.g').setAttribute('d', lit.lg); $('.edges path.b').setAttribute('d', lit.lb);
    // the ungrouped column: left-aligned, the same 12px gap between cards whatever their heights
    let top = 50;
    pane.querySelectorAll('.card.col').forEach((c) => { c.style.top = top + 'px'; top += c.offsetHeight + 12; });
    $('.ucol').hidden = !pane.querySelector('.card.col');
    const empty = !S.sessions.some((x) => x.h === 'cc');
    $('.map-empty').hidden = !empty;
  }
  function renderLog() {
    $('.lrows').innerHTML = S.logs.slice(-5).map((l) => {
      const isNew = !seen.has('log' + l.k); seen.add('log' + l.k);
      return `<div class="lrow${isNew ? ' fade' : ''}"><i>${l.ts}</i><span><b class="${l.c}">${esc(l.src)} ⇢ ${esc(l.dst)}</b>: ${esc(l.text)}</span></div>`;
    }).join('') || '<div class="lempty">no messages yet</div>';
  }
  function renderTerm() {
    $('.term-name').textContent = '⌨ ' + S.tab;
    $('.term').innerHTML = S.term.slice(-6).map((l) => `<div class="${l.cls || ''}">${l.html}</div>`).join('')
      + (S.tab === 'zsh' ? '' : '<div class="box"><span class="you">&gt;</span> <span class="cursor"></span></div>');
  }
  function render() { renderSide(); renderMap(); renderLog(); renderTerm(); $('.ide-status .chain').textContent = S.chain; }

  // ---- playback primitives -------------------------------------------------------------------------
  let token = 0, FF = false;
  const ABORT = Symbol('abort');
  const live = (t) => { if (t !== token) throw ABORT; };
  const wait = (ms, t = token) => new Promise((ok, no) => {
    if (FF) return ok();
    setTimeout(() => (t === token ? ok() : no(ABORT)), ms);
  });
  let clock = 14 * 3600 + 2 * 60 + 5;
  const stamp = () => { clock += 7 + (clock % 5); const h = Math.floor(clock / 3600), m = Math.floor(clock / 60) % 60, s = clock % 60;
    return `09/23/2026-${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`; };

  async function type(text, cls = '') {
    const line = { cls, html: '' }; S.term.push(line);
    const prompt = S.tab === 'zsh' ? '<span class="pmt">$</span> ' : '<span class="you">&gt;</span> ';
    if (FF) { line.html = prompt + esc(text); renderTerm(); return; }
    for (let i = 1; i <= text.length; i += 2) { line.html = prompt + esc(text.slice(0, i)) + '<span class="cursor"></span>'; renderTerm(); await wait(22); }
    line.html = prompt + esc(text); renderTerm(); await wait(350);
  }
  async function say(html, cls = '', ms = 420) { S.term.push({ cls, html }); renderTerm(); await wait(ms); }
  async function tab(name) { S.tab = name; S.term = []; if (find(name)) S.sel = name; render(); await wait(250); }
  async function add(s) { S.sessions.push({ state: 'active', ctx: 4, ...s }); S.sel = s.id; render(); await wait(500); }
  async function set(id, patch) { Object.assign(find(id), patch); render(); await wait(300); }
  async function log(src, dst, text, c) { S.logs.push({ k: S.logs.length, ts: stamp(), src, dst, text, c }); renderLog(); renderMap(); await wait(600); }
  async function chain(text) { S.chain = text; render(); await wait(400); }

  // pointer, overlays
  const ptr = $('.ptr');
  function at(el, dx = 0.5, dy = 0.5) {
    const a = stage.getBoundingClientRect(), b = el.getBoundingClientRect();
    return [b.left - a.left + b.width * dx, b.top - a.top + b.height * dy];
  }
  async function point(sel, dx, dy) {
    if (FF) return; const el = typeof sel === 'string' ? $(sel) : sel; if (!el) return;
    const [x, y] = at(el, dx, dy); ptr.style.opacity = 1; ptr.style.transform = `translate(${x}px,${y}px)`; await wait(650);
  }
  const hidePtr = () => { ptr.style.opacity = 0; };
  async function click(sel, dx, dy) { await point(sel, dx, dy); if (FF) return; ptr.classList.add('down'); await wait(160); ptr.classList.remove('down'); }
  async function key(label) { if (FF) return; const k = $('.keycap'); k.textContent = label; k.hidden = false; await wait(700); k.hidden = true; }
  async function pick(title, items, choice) {
    const qp = $('.qp');
    if (FF) return;
    let i = 0;
    const draw = () => { qp.innerHTML = `<div class="qp-in">${esc(title)}</div>` + items.map((it, j) =>
      `<div class="qp-it${j === i ? ' on' : ''}"><b>${esc(it[0])}</b>${it[1] ? `<em>${esc(it[1])}</em>` : ''}</div>`).join(''); };
    draw(); qp.hidden = false; await wait(700);
    while (i < choice) { i++; draw(); await wait(380); }
    await wait(350); qp.hidden = true; await wait(150);
  }
  async function input(title, value) {
    const qp = $('.qp'); if (FF) return;
    qp.innerHTML = `<div class="qp-in">${esc(title)}</div><div class="qp-val"></div>`; qp.hidden = false;
    const v = qp.querySelector('.qp-val');
    for (let i = 1; i <= value.length; i++) { v.innerHTML = esc(value.slice(0, i)) + '<span class="cursor"></span>'; await wait(70); }
    await wait(350); qp.hidden = true; await wait(150);
  }
  async function drag(id, onto) {
    if (FF) { find(id).parent = onto; render(); return; }
    const row = $(`.row[data-id="${id}"]`), target = $(`.row[data-id="${onto}"]`);
    await point(row, 0.3); ptr.classList.add('down');
    const ghost = row.cloneNode(true); ghost.classList.add('ghost'); stage.appendChild(ghost);
    const [x0, y0] = at(row, 0.3); ghost.style.transform = `translate(${x0}px,${y0}px)`;
    await wait(120);
    const [x1, y1] = at(target, 0.4); ptr.style.transform = `translate(${x1}px,${y1}px)`; ghost.style.transform = `translate(${x1}px,${y1}px)`;
    target.classList.add('drop'); await wait(700);
    ghost.remove(); ptr.classList.remove('down'); find(id).parent = onto; render(); await wait(450);
  }
  async function tip(id, html) {
    const t = $('.tip'); if (FF) return;
    const row = $(`.row[data-id="${id}"]`); await point(row, 0.35);
    const [x, y] = at(row, 0.35, 1); t.innerHTML = html; t.style.transform = `translate(${x}px,${y + 6}px)`; t.hidden = false;
    await wait(2300); t.hidden = true;
  }
  async function menu(id, items, choice) {
    const m = $('.ctx'); if (FF) return;
    const row = $(`.row[data-id="${id}"]`); await click(row, 0.4);
    const [x, y] = at(row, 0.4, 0.6);
    m.innerHTML = items.map((it, j) => `<div class="${j === choice ? 'hl' : ''}">${esc(it)}</div>`).join('');
    m.style.transform = `translate(${x}px,${y}px)`; m.hidden = false; await wait(500);
    await point(m.children[choice], 0.3); await wait(400); m.hidden = true;
  }
  async function toast(text, ms = 1400) { const t = $('.toast'); if (FF) return; t.textContent = text; t.hidden = false; await wait(ms); t.hidden = true; }

  // ---- the scenes (docs/tutorial-script.md) --------------------------------------------------------
  const newSession = async (name, folder, where, harness, s) => {
    await key('n');
    await input('Folder for the new session', folder);
    await input('Session name', name);
    await pick('Choose where to start the session', [['Create a worktree', 'isolated branch for this session'], ['Use selected folder', 'no new worktree · fine if a session already runs here']], where === 'worktree' ? 0 : 1);
    await pick('Choose the agent harness', [['Claude Code'], ['pi']], harness === 'pi' ? 1 : 0);
    await add({ name, id: name, h: harness, ...s });
  };
  // The cast: `lead` runs in the monorepo that manages the apps; each child owns one app in its own repo.
  const SCENES = [
    { t: 'Install', p: 'One command installs the backend, the harness wiring and the editor extension.', run: async () => {
      await tab('zsh');
      await type('curl -fsSL https://pengupool.nduwork.com/install.sh | bash');
      for (const l of ['Installing PenguPool v0.1.0', '✓ pengupool CLI 0.1.0', '✓ Claude Code hooks and SendMessage guard', '✓ pi extension · workflow tracker', '✓ Extension installed in Cursor'])
        await say(esc(l), l.startsWith('✓') ? 'ok' : 'dim', 330);
      await say('Done. Reload your editor window, then open the PenguPool view.', 'dim', 900);
    } },
    { t: 'Start a pool', p: 'Press n for each session: pick its repo, a new worktree or the folder itself, and the harness.', run: async () => {
      await point('.ide-side', 0.5, 0.25);
      await newSession('lead', '~/code/shop', 'folder', 'cc', { repo: 'shop', ctx: 6 });
      await tab('lead');
      await newSession('api', '~/code/shop-api', 'worktree', 'cc', { repo: 'shop-api-wt-token', ctx: 3 });
      await newSession('web', '~/code/shop-web', 'folder', 'cc', { repo: 'shop-web', ctx: 3 });
      await add({ name: 'deploy', id: 'deploy', h: 'cc', repo: 'shop-deploy', ctx: 2 });
      await add({ name: 'payments', id: 'payments', h: 'cc', repo: 'shop-payments', state: 'waiting', ctx: 5 });  // stays ungrouped
      await newSession('docs-writer', '~/code/shop', 'folder', 'pi', { repo: 'shop', state: 'waiting', ctx: 2 });
      S.sel = 'lead'; render(); hidePtr();
    } },
    { t: 'Group the children', p: 'Drag each app session onto the monorepo lead; payments stays on its own. The map becomes the tree every session sees.', run: async () => {
      await drag('api', 'lead'); await drag('web', 'lead'); await drag('deploy', 'lead');
      S.sel = 'lead'; render(); hidePtr(); await wait(600);
    } },
    { t: 'Brief the parent', p: 'Tell the parent about every new child and how you plan to use it.', run: async () => {
      if (S.tab !== 'lead') await tab('lead');
      await type('I added api, web and deploy under you. api owns the backend (shop-api), web the frontend (shop-web), deploy the deployment (shop-deploy). Route work to them.');
      await say('<span class="dot">⏺</span> Triage: mine', '', 450);
      await say('<span class="dot">⏺</span> Got it: backend → api, frontend → web, releases → deploy. I keep the monorepo and the plan.', '', 700);
      for (const [id, ctx] of [['api', 9], ['web', 8], ['deploy', 6]]) find(id).ctx = ctx;
      find('lead').ctx = 14; render();
      await tip('api', '<b>api</b><br>Backend: the shop-api service<br><em>set by the session (ROLE REQUIRED)</em>');
      hidePtr();
    } },
    { t: 'Ask the top', p: 'Talk to the top. Triage routes each part to the repo session that owns it.', run: async () => {
      if (S.tab !== 'lead') await tab('lead');
      await type('Add token refresh to login.');
      await say('<span class="dot">⏺</span> Triage: → api, web', '', 350);
      await say('  ⎿ SendMessage api · SendMessage web', 'dim', 200);
      await log('lead', 'api', 'add the token refresh endpoint', 'lg');
      await set('api', { ctx: 24 });
      await log('lead', 'web', 'wire refresh into the login form', 'lg');
      await set('web', { state: 'active', ctx: 19 });
      await chain('[ship-auth] api ● → web ○ → deploy ○');
      await wait(900);
      await log('api', 'lead', 'endpoint done, tests pass', 'lb');
      await set('api', { state: 'waiting', ctx: 41 });
      await chain('[ship-auth] api ✓ → web ● → deploy ○');
      await say('<span class="dot">⏺</span> api is done. Waiting on web, then deploy to staging.', '', 900);
    } },
    { t: 'Direct line and approvals', p: 'Tag @session to reach a session outside your tree for one prompt. Approval badges show who is waiting on you.', run: async () => {
      await tab('web');
      await type('@payments will token refresh log users out of checkout?');
      await log('web', 'payments', 'will token refresh end checkout sessions?', 'lo');
      await log('payments', 'web', 'no: checkout re-reads the token on each call', 'lo');
      await set('web', { state: 'waiting', ctx: 33 });
      await chain('[ship-auth] api ✓ → web ✓ → deploy ●');
      await log('lead', 'deploy', 'ship shop-api and shop-web to staging', 'lg');
      await set('deploy', { state: 'blocked', ctx: 22 });
      await click(`.card[data-id="deploy"]`);
      await tab('deploy');
      await say('<span class="warn">Allow Bash(make deploy ENV=staging)?</span>', '', 300);
      await say('❯ 1. Yes   2. No', 'dim', 700);
      await key('⏎');
      await set('deploy', { state: 'active', ctx: 28 });
      await say('<span class="dot">⏺</span> Staging is live: shop-api v1.8.0, shop-web v2.3.0.', '', 800);
      hidePtr();
    } },
    { t: 'Keep the tree healthy', p: 'Restart in place after an update. Compact with c, never /new or /clear.', run: async () => {
      await menu('api', ['Open / Focus Session', 'Group Under…', 'Rename', 'Describe Role…', 'Compact (/compact)', 'Restart & Resume (Shift+R)', 'Close'], 5);
      await set('api', { blink: true });
      await toast('PenguPool: restarting api…');
      await tab('api');
      await set('api', { blink: false, ctx: 41 });
      await say('↻ Resumed in place: same session, same worktree, same group', 'dim', 700);
      await set('lead', { ctx: 71 });  // a long-running parent: time to compact (the red ctx badge)
      await point(`.row[data-id="lead"]`, 0.4); await tab('lead'); await key('c');
      await say('✓ Compacted · lead is still the parent of api, web and deploy', 'ok', 500);
      await set('lead', { ctx: 9 });
      await chain('[ship-auth] api ✓ → web ✓ → deploy ✓');
      await log('deploy', 'lead', 'staging deploy done, smoke tests green', 'lb');
      hidePtr(); await wait(1200);
    } },
  ];

  // ---- controls ------------------------------------------------------------------------------------
  const list = document.getElementById('chapters'), playBtn = document.getElementById('tplay');
  list.innerHTML = SCENES.map((s, i) => `<li><button type="button" data-i="${i}"><span>${i + 1}</span>${esc(s.t)}</button></li>`).join('');
  let cur = 0, playing = !REDUCED && !RECORD, visible = false;
  function caption(i) {
    tut.querySelector('.cap-n').textContent = i + 1;
    tut.querySelector('.cap-t').textContent = SCENES[i].t;
    tut.querySelector('.cap-p').textContent = SCENES[i].p;
    list.querySelectorAll('button').forEach((b, j) => b.setAttribute('aria-current', j === i ? 'step' : 'false'));
  }
  function resetTo(i) { // state at the start of scene i
    S = fresh(); seen.clear(); $('.cards').innerHTML = ''; clock = 14 * 3600 + 2 * 60 + 5;
    FF = true; const chainRuns = SCENES.slice(0, i).reduce((p, s) => p.then(s.run), Promise.resolve());
    return chainRuns.then(() => { FF = false; stage.querySelectorAll('.fade').forEach((e) => e.classList.remove('fade')); seen.clear(); render(); stage.querySelectorAll('.fade').forEach((e) => e.classList.remove('fade')); });
  }
  function halt() { // stop the timeline and clear anything a scene left mid-animation
    token++;
    ['.qp', '.ctx', '.tip', '.toast', '.keycap'].forEach((s) => { $(s).hidden = true; });
    stage.querySelectorAll('.ghost').forEach((g) => g.remove());
    stage.querySelectorAll('.drop').forEach((r) => r.classList.remove('drop'));
    ptr.classList.remove('down'); hidePtr();
  }
  async function play(i, auto) {
    halt();  // bumps token: anything still playing aborts
    const t = ++token; cur = i; caption(i);
    await resetTo(i); live(t);
    if (REDUCED && !RECORD) { FF = true; await SCENES[i].run(); FF = false; render(); return; }
    await wait(500, t);
    await SCENES[i].run(); live(t);
    if (RECORD) { await wait(800, t); window.__done = true; return; }
    await wait(1600, t);
    if (auto && playing) start((i + 1) % SCENES.length, true);
  }
  const start = (i, auto) => play(i, auto).catch((e) => { if (e !== ABORT) throw e; });
  list.addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    start(Number(b.dataset.i), playing);
  });
  playBtn.addEventListener('click', () => {
    playing = !playing; playBtn.textContent = playing ? '❚❚ Pause' : '▶ Play';
    if (playing) start(cur, true); else halt();
  });
  playBtn.textContent = playing ? '❚❚ Pause' : '▶ Play';
  if (REDUCED) playBtn.hidden = true;

  if (RECORD) { document.body.classList.add('record'); start(RECORD - 1, false); return; }
  S = fresh(); render(); caption(0);
  if (REDUCED) { start(0, false); return; }
  new IntersectionObserver((es) => {
    const v = es[0].isIntersecting;
    if (v && !visible && playing) start(cur, true);
    if (!v && visible) halt(); // pause off screen; resumes the current scene from its start
    visible = v;
  }, { threshold: 0.35 }).observe(tut);
})();
