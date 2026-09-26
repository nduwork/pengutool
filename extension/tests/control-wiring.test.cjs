const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

/** Load a TypeScript source in the current realm with a small require map. */
function load(file, deps) {
  const filename = path.join(__dirname, '../src', file);
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021 },
  }).outputText;
  const mod = new Module(filename, module);
  mod.filename = filename;
  mod.paths = Module._nodeModulePaths(path.dirname(filename));
  const originalRequire = mod.require.bind(mod);
  mod.require = (id) => (id in deps ? deps[id] : originalRequire(id));
  mod._compile(code, filename);
  return mod.exports;
}

/** Fake VS Code whose `pengupool.terminalMode` is `control`, with a recording ControlTerminal. */
function harness({ controlAvailable = true } = {}) {
  const terminals = [];
  const shown = [];        // [pane, force] passed to showPane
  const warnings = [];
  let closeHandler = () => {};
  class ControlTerminal {
    constructor(options) { this.options = options; this.closed = false; }
    showPane(pane, force) { shown.push([pane, force]); return Promise.resolve(); }
    handleInput() {}
    setDimensions() {}
    close() { this.closed = true; }
  }
  const vscode = {
    EventEmitter: class { event = () => ({ dispose() {} }); fire() {} dispose() {} },
    workspace: { getConfiguration: () => ({ get: (key, dflt) => (key === 'terminalMode' ? 'control' : dflt) }) },
    commands: { executeCommand: async () => {} },
    ProgressLocation: { Notification: 15 },
    window: {
      terminals,
      onDidOpenTerminal: () => ({ dispose() {} }),
      onDidChangeActiveTerminal: () => ({ dispose() {} }),
      onDidCloseTerminal: (handler) => { closeHandler = handler; return { dispose() {} }; },
      createTerminal: (options) => {
        const terminal = { name: options.name, options, shows: 0, exitStatus: undefined,
                           show() { this.shows++; }, dispose() {} };
        terminals.push(terminal);
        return terminal;
      },
      showErrorMessage: () => {},
      showWarningMessage: (message) => { warnings.push(message); return Promise.resolve(undefined); },
      withProgress: async (_options, task) => task(),
    },
  };
  const current = { pane: '%7', harness: 'cc', mode: 'control' };
  const ctl = { runCtl: async () => ({ code: 0, stderr: '', stdout: JSON.stringify(
    { command: 'tmux attach', pane: current.pane, cwd: '/repo', harness: current.harness }) }) };
  vscode.workspace.getConfiguration = () => ({ get: (key, dflt) => (key === 'terminalMode' ? current.mode : dflt) });
  const { TerminalManager } = load('terminals.ts', { vscode, './util': ctl, './controlTerminal': { ControlTerminal },
                                                    './controlSession': { controlAvailable: () => controlAvailable } });
  const manager = new TerminalManager({ workspaceState: { get: () => ({}), update: async () => {} } });
  return { manager, terminals, shown, current, warnings, close: (terminal) => closeHandler(terminal) };
}

const node = (id, harness = 'cc') => ({ id, name: id, harness, children: [] });

test('control mode opens one pty terminal per harness and repaints it on switch', async () => {
  const h = harness();
  assert.equal(await h.manager.switchTo(node('a')), true);
  assert.equal(h.terminals.length, 1, 'one terminal for the harness');
  const pty = h.terminals[0].options.pty;
  assert.ok(pty && typeof pty.showPane === 'function', 'the terminal is backed by a control pty');
  assert.equal(h.terminals[0].options.shellPath, undefined, 'no tmux client is spawned');
  assert.deepEqual(h.shown.at(-1), ['%7', false]);

  h.current.pane = '%8';
  assert.equal(await h.manager.switchTo(node('b')), true);
  assert.equal(h.terminals.length, 1, 'switching sessions reuses the terminal');
  assert.deepEqual(h.shown.at(-1), ['%8', false], 'the same control terminal repaints the new pane');
});

test('a restart forces a repaint of the unchanged pane', async () => {
  const h = harness();
  await h.manager.switchTo(node('a'));
  h.shown.length = 0;
  await h.manager.restart(node('a'));
  assert.deepEqual(h.shown.at(-1), ['%7', true], 'force repaint so the restarted process is shown');
});

test('pi gets its own control terminal on its own tmux socket', async () => {
  const h = harness();
  await h.manager.switchTo(node('a', 'cc'));
  h.current.pane = '%3';
  h.current.harness = 'pi';
  await h.manager.switchTo(node('b', 'pi'));
  assert.equal(h.terminals.length, 2, 'a second terminal for the second harness');
  const pi = h.terminals[1].options.pty;
  assert.notEqual(h.terminals[0].options.pty, pi);
});

test('closing a control terminal disposes its session and the next open creates a new one', async () => {
  const h = harness();
  await h.manager.switchTo(node('a'));
  const first = h.terminals[0].options.pty;
  h.close(h.terminals[0]);
  assert.equal(first.closed, true, 'the tmux client was disposed with the terminal');

  h.current.pane = '%9';
  await h.manager.switchTo(node('b'));
  assert.equal(h.terminals.length, 2, 'reopening makes a fresh terminal instead of leaking the old one');
  assert.notEqual(h.terminals[1].options.pty, first);
});

test('switching to tmux mode closes the control client, and back makes a fresh one', async () => {
  const h = harness();
  await h.manager.switchTo(node('a'));
  const control = h.terminals[0].options.pty;

  h.current.mode = 'tmux';
  h.current.pane = '%8';
  await h.manager.switchTo(node('b'));
  assert.equal(control.closed, true, 'the control client is closed synchronously, not left in the map');
  assert.equal(h.terminals.length, 2, 'tmux mode opens a shell terminal');
  assert.ok(h.terminals[1].options.shellPath, 'the tmux terminal runs a real tmux client');

  h.current.mode = 'control';
  h.current.pane = '%9';
  await h.manager.switchTo(node('c'));
  assert.equal(h.terminals.length, 3);
  assert.notEqual(h.terminals[2].options.pty, control, 'a fresh control client, not the closed one');
  assert.deepEqual(h.shown.at(-1), ['%9', false], 'the new control terminal shows the requested pane');
});

test('a failed paint clears the current session so the next selection retries', async () => {
  const h = harness();
  await h.manager.switchTo(node('a'));
  const pty = h.terminals[0].options.pty;
  pty.onPaintFailed();  // the control session reports a failed capture

  h.shown.length = 0;
  assert.equal(await h.manager.switchTo(node('a')), true);
  assert.equal(h.terminals.length, 1, 'the same terminal is reused');
  assert.deepEqual(h.shown.at(-1), ['%7', false], 'the paint is retried instead of returning early');
});

test('falls back to tmux mode when the native addon is unavailable', async () => {
  const h = harness({ controlAvailable: false });
  assert.equal(await h.manager.switchTo(node('a')), true);
  assert.ok(h.terminals[0].options.shellPath, 'runs a real tmux client instead of a control pty');
  assert.equal(h.warnings.length, 1, 'warns once about the fallback');
});
