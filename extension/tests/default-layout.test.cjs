const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function loadLayout({ initiallyVisible = false } = {}) {
  const calls = [];
  let visibility;
  const tree = {
    visible: initiallyVisible,
    selection: [],
    onDidChangeVisibility(callback) { visibility = callback; return { dispose() {} }; },
  };
  const vscode = {
    ViewColumn: { One: 1, Beside: -2 },
    commands: { executeCommand: async (command) => { calls.push(['command', command]); } },
  };
  const mapPanel = { MapPanel: { show: (...args) => calls.push(['map', ...args]) } };
  const logPanel = { LogPanel: { show: (...args) => calls.push(['log', ...args]) } };
  const terminals = {
    async showDefault(roots, selected) { calls.push(['terminal', roots, selected]); return true; },
  };
  const filename = path.join(__dirname, '../src/defaultLayout.ts');
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, {
    exports,
    require: (id) => {
      if (id === 'vscode') return vscode;
      if (id === './mapPanel') return mapPanel;
      if (id === './logPanel') return logPanel;
      if (id === './serveClient' || id === './terminals') return {};
      throw new Error('Unexpected dependency: ' + id);
    },
  }, { filename });
  const layout = new exports.DefaultLayout({ extensionUri: 'extension' }, tree, terminals);
  return { calls, layout, tree, show: () => visibility({ visible: true }) };
}

const snapshot = { roots: [{ id: 'root', children: [] }], msgs: [], topo_hash: 'one' };

test('revealing the PenguPool view opens the reference layout once', async () => {
  const h = loadLayout();
  h.layout.update(snapshot);
  h.show();
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(h.calls.map((call) => call[0]), ['map', 'log', 'command', 'terminal']);
  assert.equal(h.calls[0][3], 1);
  assert.equal(h.calls[1][2], -2);
  assert.equal(h.calls[2][1], 'workbench.action.positionPanelBottom');

  h.show();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.calls.length, 4);
});

test('a restored visible view waits for its first snapshot before opening a terminal', async () => {
  const h = loadLayout({ initiallyVisible: true });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(h.calls.map((call) => call[0]), ['map', 'log', 'command']);

  h.layout.update(snapshot);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.calls.at(-1)[0], 'terminal');
});
