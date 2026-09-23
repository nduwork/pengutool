const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function harness() {
  const commands = [];
  const vscode = {
    commands: { executeCommand: async (...args) => { commands.push(args); } },
    EventEmitter: class { event = () => ({ dispose() {} }); fire() {} dispose() {} },
  };
  const modules = {};
  const load = (name) => {
    if (modules[name]) return modules[name];
    const filename = path.join(__dirname, '../src', name + '.ts');
    const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021 },
    }).outputText;
    const exports = modules[name] = {};
    vm.runInNewContext(code, {
      exports,
      require: (id) => {
        if (id === 'vscode') return vscode;
        if (id === './harness') return load('harness');
        if (id === './serveClient') return {};
        if (id === './sessionState') return { SESSION_STATES: {}, SESSION_STATE_CSS: '' };
        throw new Error('Unexpected dependency: ' + id);
      },
    }, { filename });
    return exports;
  };
  return { commands, load };
}

const cc = (id, name, children = []) => ({ id, name, children });   // no harness field => cc
const pi = (id, name, children = []) => ({ id, name, harness: 'pi', children });
const snap = (roots) => ({
  topo_hash: 'h', roots,
  cross: [['alpha', 'alpha-kid', 'cc link'], ['beta', 'alpha', 'mixed link'], ['beta', 'beta-kid', 'pi link']],
  msgs: [['t', 'alpha', 'alpha-kid', 'cc msg', false], ['t', 'alpha', 'beta', 'mixed msg', true],
         ['t', 'beta-kid', 'beta', 'pi msg', true], ['t', 'gone', 'beta', 'unknown msg', true]],
});
const ccTree = () => cc('c1', 'alpha', [cc('c2', 'alpha-kid')]);
const piTree = () => pi('p1', 'beta', [pi('p2', 'beta-kid')]);

test('harnessesIn and bothHarnesses see cc-only, pi-only and both', () => {
  const { harnessesIn, bothHarnesses } = harness().load('harness');
  assert.deepEqual([...harnessesIn([ccTree()])], ['cc']);
  assert.deepEqual([...harnessesIn([piTree()])], ['pi']);
  assert.equal(bothHarnesses([ccTree()]), false);
  assert.equal(bothHarnesses([piTree()]), false);
  assert.equal(bothHarnesses([ccTree(), piTree()]), true);
});

test('splitByHarness keeps one harness and only links/messages between its own sessions', () => {
  const { splitByHarness } = harness().load('harness');
  const both = snap([ccTree(), piTree()]);
  const ccOnly = splitByHarness(both, 'cc');
  assert.deepEqual(ccOnly.roots.map((root) => root.id), ['c1']);
  assert.deepEqual(ccOnly.cross.map((link) => link[2]), ['cc link']);
  assert.deepEqual(ccOnly.msgs.map((msg) => msg[3]), ['cc msg']);
  const piOnly = splitByHarness(both, 'pi');
  assert.deepEqual(piOnly.roots.map((root) => root.id), ['p1']);
  assert.deepEqual(piOnly.cross.map((link) => link[2]), ['pi link']);
  assert.deepEqual(piOnly.msgs.map((msg) => msg[3]), ['pi msg']);
  assert.notEqual(ccOnly.topo_hash, piOnly.topo_hash);
  assert.equal(both.roots.length, 2, 'input snapshot untouched');
  assert.deepEqual(splitByHarness(snap([piTree()]), 'cc').roots, []);
});

test('bothHarnesses context key is true only while both harnesses run, set on change', () => {
  const h = harness();
  const sync = h.load('harness').bothHarnessesContext();
  sync([ccTree()]);
  sync([ccTree()]);
  sync([ccTree(), piTree()]);
  sync([piTree()]);
  assert.deepEqual(h.commands, [
    ['setContext', 'pengupool.bothHarnesses', false],
    ['setContext', 'pengupool.bothHarnesses', true],
    ['setContext', 'pengupool.bothHarnesses', false],
  ]);
});

function sessionsViews() {
  const h = harness();
  const { SessionsView } = h.load('sessionsView');
  const nodes = new Map();
  const provider = {
    find: (id) => nodes.get(id),
    update(s) { nodes.clear(); const walk = (n) => { nodes.set(n.id, n); n.children.forEach(walk); }; s.roots.forEach(walk); },
  };
  const make = (harnessName) => {
    const view = new SessionsView(provider, harnessName);
    const posts = [];
    let receive;
    view.resolveWebviewView({
      visible: true,
      webview: { postMessage: async (m) => { posts.push(m); }, onDidReceiveMessage: (fn) => { receive = fn; return { dispose() {} }; } },
      onDidChangeVisibility: () => ({ dispose() {} }),
    });
    receive({ type: 'ready' });
    return { view, posts, send: (m) => receive(m), roots: () => posts.at(-1)?.snapshot.roots.map((r) => r.id) };
  };
  const main = make(undefined), piView = make('pi');
  const update = (s) => { provider.update(s); main.view.update(s); piView.view.update(s); };
  return { main, piView, update, commands: h.commands };
}

test('Sessions lists everything while one harness runs and splits by harness when both do', async () => {
  const v = sessionsViews();
  v.update(snap([piTree()]));
  assert.deepEqual(v.main.roots(), ['p1'], 'a pi-only machine sees pi sessions in the main view');
  v.update(snap([ccTree()]));
  assert.deepEqual(v.main.roots(), ['c1']);
  v.update(snap([ccTree(), piTree()]));
  assert.deepEqual(v.main.roots(), ['c1']);
  assert.deepEqual(v.piView.roots(), ['p1']);
});

test('a selection made in the pi Sessions view drives selection-based commands', async () => {
  const v = sessionsViews();
  v.update(snap([ccTree(), piTree()]));
  await v.piView.send({ type: 'select', id: 'p2' });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(v.main.view.selection[0].id, 'p2');
  assert.equal(v.main.posts.at(-1).selectedId, 'p2');
  await v.piView.send({ type: 'command', command: 'pengupool.rename', id: 'p1' });
  assert.equal(v.commands.at(-1)[0], 'pengupool.rename');
  assert.equal(v.commands.at(-1)[1].id, 'p1');
});
