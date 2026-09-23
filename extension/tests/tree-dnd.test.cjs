const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function harness() {
  const requests = [], warnings = [], errors = [];
  const vscode = {
    EventEmitter: class {
      event = () => ({ dispose() {} });
      fire() {}
    },
    DataTransferItem: class {
      constructor(value) { this.value = value; }
      async asString() { return JSON.stringify(this.value); }
    },
    TreeItem: class {},
    TreeItemCollapsibleState: { None: 0, Expanded: 2 },
    window: {
      showWarningMessage: (message) => warnings.push(message),
      showErrorMessage: (message) => errors.push(message),
    },
  };
  const ctl = {
    runCtl: async (args) => {
      requests.push(args);
      return { code: 0, stdout: '', stderr: '' };
    },
  };
  const filename = path.join(__dirname, '../src/sessionsTree.ts');
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, {
    exports,
    require: (id) => {
      if (id === 'vscode') return vscode;
      if (id === './util') return ctl;
      if (id === './serveClient') return {};
      if (id === './sessionState') return { SESSION_STATES: {} };
      throw new Error('Unexpected dependency: ' + id);
    },
  }, { filename });
  const provider = new exports.SessionsProvider();
  const child = { id: 'child', name: 'child', children: [] };
  const root = { id: 'root', name: 'root', children: [child] };
  const other = { id: 'other', name: 'other', children: [] };
  provider.update({ roots: [root, other] });
  const transfer = new Map();
  return { provider, transfer, root, child, other, requests, warnings, errors };
}

test('dropping a session on another session groups it underneath', async () => {
  const h = harness();
  h.provider.handleDrag([h.child], h.transfer);
  await h.provider.handleDrop(h.other, h.transfer);
  assert.deepEqual([...h.requests[0]], ['group', 'child', 'other']);
});

test('dropping a session on empty tree space moves it to top level', async () => {
  const h = harness();
  h.provider.handleDrag([h.child], h.transfer);
  await h.provider.handleDrop(undefined, h.transfer);
  assert.deepEqual([...h.requests[0]], ['group', 'child', '']);
});

test('drag and drop rejects hierarchy cycles', async () => {
  const h = harness();
  h.provider.handleDrag([h.root], h.transfer);
  await h.provider.handleDrop(h.child, h.transfer);
  assert.equal(h.requests.length, 0);
  assert.equal(h.warnings.length, 1);
});
