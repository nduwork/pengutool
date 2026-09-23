const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

function loadCommands() {
  const vscode = { Uri: { file: (fsPath) => ({ fsPath }) } };
  const filename = path.join(__dirname, '../src/commands.ts');
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, {
    exports, process,
    require: (id) => id === 'vscode' ? vscode : id === './util' ? {} : {},
  }, { filename });
  return exports;
}

test('Add Previous remembers and restores its last selected folder', async () => {
  const commands = loadCommands();
  const values = new Map();
  const context = { globalState: {
    get: (key) => values.get(key),
    update: async (key, value) => values.set(key, value),
  } };
  const fallback = { fsPath: '/workspace' };

  assert.equal(commands.lastAddDirectory(context, fallback), fallback);
  await commands.rememberAddDirectory(context, { fsPath: '/repos/last-used' });
  assert.equal(commands.lastAddDirectory(context, fallback).fsPath, '/repos/last-used');
});
