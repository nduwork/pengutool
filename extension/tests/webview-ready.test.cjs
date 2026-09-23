const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function loadPanel(sourceName) {
  const posts = [], commands = [];
  let receive;
  const webview = {
    cspSource: 'vscode-resource:',
    html: '',
    asWebviewUri: (uri) => uri,
    postMessage: async (message) => { posts.push(message); return true; },
    onDidReceiveMessage: (callback) => { receive = callback; return { dispose() {} }; },
  };
  const panel = {
    webview,
    reveal() {},
    dispose() {},
    onDidDispose: () => ({ dispose() {} }),
  };
  const vscode = {
    ViewColumn: { One: 1, Beside: -2 },
    Uri: { joinPath: (...parts) => parts.join('/') },
    commands: { executeCommand: async (...args) => { commands.push(args); } },
    window: { createWebviewPanel: () => panel },
  };
  const load = (name) => {
    const filename = path.join(__dirname, '../src', name + '.ts');
    const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021 },
    }).outputText;
    const exports = {};
    vm.runInNewContext(code, { exports, require }, { filename });
    return exports;
  };
  const require = (id) => {
      if (id === 'vscode') return vscode;
      if (id === './harness') return load('harness');
      if (id === './serveClient') return {};
      if (id === './sessionState') return {
        SESSION_STATES: {
          active: { symbol: '●', label: 'Active' },
          waiting: { symbol: '◷', label: 'Waiting' },
          stale: { symbol: '○', label: 'Stale' },
          blocked: { symbol: '?', label: 'Approval' },
        },
        SESSION_STATE_CSS: '.state-active { --state-color: green; }',
      };
      throw new Error('Unexpected dependency: ' + id);
  };
  const exports = load(sourceName);
  const data = () => posts.filter((message) => message?.type !== 'tabs' && message?.type !== 'selection');
  return {
    data, tabs: () => posts.filter((message) => message?.type === 'tabs'),
    exports, posts, commands,
    html: () => webview.html,
    send: (message) => receive(message),
    ready: () => receive({ type: 'ready' }),
  };
}

const snapshot = { roots: [], msgs: [['now', 'a', 'b', 'hello', false]], topo_hash: 'one' };

test('map replays the current snapshot when its webview becomes ready', () => {
  const h = loadPanel('mapPanel');
  h.exports.MapPanel.show({ extensionUri: 'extension' }, snapshot, 1);
  assert.equal(h.data().length, 1);
  h.ready();
  assert.equal(h.data().length, 2);
  assert.equal(h.data()[1].topo_hash, 'one');
});

test('map node messages invoke the same session switch command as the tree', () => {
  const h = loadPanel('mapPanel');
  h.exports.MapPanel.show({ extensionUri: 'extension' }, snapshot, 1);
  h.send({ type: 'select', id: 'session-1' });
  assert.deepEqual(h.commands[0], ['pengupool.switch', 'session-1']);
});

test('map keeps the selected session separate from its state', () => {
  const h = loadPanel('mapPanel');
  const panel = h.exports.MapPanel.show({ extensionUri: 'extension' }, snapshot, 1);
  panel.select('session-1');
  assert.equal(h.posts.at(-1).type, 'selection');
  assert.equal(h.posts.at(-1).id, 'session-1');
  assert.match(h.html(), /node\.selected \.selection/);
});

test('log replays current messages when its webview becomes ready', () => {
  const h = loadPanel('logPanel');
  h.exports.LogPanel.show(snapshot, 1);
  assert.equal(h.data().length, 1);
  h.ready();
  assert.equal(h.data().length, 2);
  assert.equal(h.data()[1][0][3], 'hello');
});

test('log timestamps use compact local system time', () => {
  const h = loadPanel('logPanel');
  const local = new Date(2026, 0, 2, 3, 4, 5);
  assert.equal(h.exports.localLogTime(local.toISOString()), '01/02/2026-03:04:05');
  assert.equal(h.exports.localLogTime('not-a-time'), 'not-a-time');
});

test('log explains message direction colors', () => {
  const h = loadPanel('logPanel');
  h.exports.LogPanel.show(snapshot, 1);
  assert.match(h.html(), /class="g">parent → child/);
  assert.match(h.html(), /class="b">child → parent/);
  assert.match(h.html(), /class="o">@session \(user-tagged\)/);
});

test('log rows preview one line and expand on click', () => {
  const h = loadPanel('logPanel');
  h.exports.LogPanel.show(snapshot, 1);
  const html = h.html();
  assert.match(html, /text-overflow:ellipsis/);          // one-line preview
  assert.match(html, /\.row\.open \.msg \{ white-space:pre-wrap/); // expand to full
  assert.match(html, /addEventListener\('click'/);       // click toggles expansion
  assert.match(html, /dataset\.k/);                     // open-state keyed per message
});

test('map session names use the editor theme foreground', () => {
  const h = loadPanel('mapPanel');
  h.exports.MapPanel.show({ extensionUri: 'extension' }, snapshot, 1);
  assert.match(h.html(), /\.nm \{ color:var\(--vscode-editor-foreground/);
});

test('map node labels stay inside bounded cards', () => {
  const h = loadPanel('mapPanel');
  h.exports.MapPanel.show({ extensionUri: 'extension' }, snapshot, 1);
  assert.match(h.html(), /overflow-wrap:anywhere/);
  assert.match(h.html(), /text-overflow:ellipsis/);
  assert.match(h.html(), /foreignObject/);
});

// A DOM just big enough for the map script: text is 6px per character, a card probe 14px per line + 12px padding.
function fakeDom() {
  const make = () => {
    const e = {
      style: {}, children: [], textContent: '', className: '',
      setAttribute(k, v) { if (k === 'class') { e.className = v; } }, appendChild(c) { e.children.push(c); },
      addEventListener() {}, set innerHTML(_) { e.children = []; },
      getBoundingClientRect: () => (/\bprobe\b/.test(e.className)
        ? { height: e.children.length * 14 + 12 } : { width: e.textContent.length * 6 }),
    };
    return e;
  };
  return { getElementById: make, querySelectorAll: () => [], createElementNS: make, body: make() };
}

test('map has a refresh button that redraws and asks for a fresh snapshot', () => {
  const h = loadPanel('mapPanel');
  h.exports.MapPanel.show({ extensionUri: 'extension' }, snapshot, 1);
  assert.match(h.html(), /id="refresh"/);
  assert.match(h.html(), /type:'refresh'/);
  const before = h.data().length;
  h.send({ type: 'refresh' });
  assert.equal(h.data().length, before + 1);
});

test('map refreshes workflow and context without a topology change', () => {
  const h = loadPanel('mapPanel');
  h.exports.MapPanel.show({ extensionUri: 'extension' }, snapshot, 1);
  const script = [...h.html().matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].at(-1)[1];
  const sandbox = { acquireVsCodeApi: () => ({ postMessage() {} }), document: fakeDom(), window: { addEventListener() {} } };
  vm.createContext(sandbox);
  vm.runInContext(script, sandbox);
  vm.runInContext(`
    const card = {grp: {setAttribute() {}}, state: {}, nm: {}, meta: {}, title: {}, chain: {style: {}}};
    nodeEls.set('one', card);
    restyle({roots:[{id:'one', name:'One', repo:'repo', state:'active', ctx_pct:68,
                    status:'[fix] diagnose ● → verify ○', children:[]}]});
    if(card.chain.textContent !== '[fix] diagnose ● → verify ○') throw Error('missing chain');
    restyle({roots:[{id:'one', name:'One', repo:'repo', state:'active', ctx_pct:2,
                    status:'[fix] diagnose ✓ → verify ●', children:[]}]});
    if(card.chain.textContent !== '[fix] diagnose ✓ → verify ●') throw Error('stale chain');
    if(!card.meta.textContent.startsWith('2%')) throw Error('stale context');
  `, sandbox);
});

const cc = (id, name, children = []) => ({ id, name, harness: 'cc', state: 'active', children });
const pi = (id, name, children = []) => ({ id, name, harness: 'pi', state: 'active', children });
const mixed = {
  topo_hash: 'mix', roots: [cc('c1', 'alpha'), pi('p1', 'beta')], cross: [],
  msgs: [['now', 'alpha', 'alpha', 'to cc', false], ['now', 'beta', 'beta', 'to pi', true]],
};

for (const [name, show] of [
  ['map', (h, snap) => h.exports.MapPanel.show({ extensionUri: 'extension' }, snap, 1)],
  ['log', (h, snap) => h.exports.LogPanel.show(snap, 1)],
]) {
  test(`${name} shows Claude Code | pi tabs only while both harnesses run`, () => {
    const h = loadPanel(name + 'Panel');
    const panel = show(h, { ...mixed, roots: [pi('p1', 'beta')] });
    assert.match(h.html(), /data-harness="cc"[^>]*>Claude Code</);
    assert.match(h.html(), /data-harness="pi"[^>]*>pi</);
    assert.equal(h.tabs().at(-1).shown, false);
    panel.update(mixed);
    assert.equal(h.tabs().at(-1).shown, true);
    assert.equal(h.tabs().at(-1).active, 'cc');
  });

  test(`${name} tab click filters to that harness without a backend round-trip`, () => {
    const h = loadPanel(name + 'Panel');
    show(h, mixed);
    const labels = () => name === 'map'
      ? h.data().at(-1).roots.map((root) => root.name)
      : h.data().at(-1).map((message) => message[3]);
    assert.deepEqual(labels(), name === 'map' ? ['alpha'] : ['to cc']);
    h.send({ type: 'tab', harness: 'pi' });
    assert.equal(h.tabs().at(-1).active, 'pi');
    assert.deepEqual(labels(), name === 'map' ? ['beta'] : ['to pi']);
  });

  test(`${name} defaults to the selected session's harness until a tab is picked`, () => {
    const h = loadPanel(name + 'Panel');
    const panel = show(h, mixed);
    panel.select('p1');
    assert.equal(h.tabs().at(-1).active, 'pi');
    h.send({ type: 'tab', harness: 'cc' });
    panel.select('p1');
    assert.equal(h.tabs().at(-1).active, 'cc');
  });
}

test('map relayouts on a tab switch but not on a status-only tick', () => {
  const h = loadPanel('mapPanel');
  const panel = h.exports.MapPanel.show({ extensionUri: 'extension' }, mixed, 1);
  const hash = () => h.data().at(-1).topo_hash;
  const first = hash();
  panel.update({ ...mixed, roots: mixed.roots.map((root) => ({ ...root, state: 'waiting' })) });
  assert.equal(hash(), first);
  h.send({ type: 'tab', harness: 'pi' });
  assert.notEqual(hash(), first);
});

test('map cards stay compact without a chain and grow with its length', () => {
  const h = loadPanel('mapPanel');
  h.exports.MapPanel.show({ extensionUri: 'extension' }, snapshot, 1);
  const script = [...h.html().matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].at(-1)[1];
  const sandbox = { acquireVsCodeApi: () => ({ postMessage() {} }), document: fakeDom(), window: { addEventListener() {} } };
  vm.createContext(sandbox);
  vm.runInContext(script, sandbox);
  const size = (status, ctx = 12) => vm.runInContext(
    `cardSize({name:'worker', repo:'repo', ctx_pct:${ctx}, status:${JSON.stringify(status)}, children:[]})`, sandbox);
  const compact = size(''), short = size('[fix] a ●'), long = size('[harness-tabs] ext ● → tui ○ → verify ○ → pr ○ → release ○');
  assert.deepEqual({ ...compact }, { width: 140, height: 3 * 14 + 12 + 2 }, 'state, name and meta lines only (+2px slack)');
  assert.ok(short.height > compact.height);
  assert.ok(long.width > short.width || long.height > short.height);
  assert.ok(long.width * long.height > short.width * short.height);
  assert.deepEqual({ ...size('', 100) }, { ...size('', 2) }, 'ctx % changes never resize a card');
});
