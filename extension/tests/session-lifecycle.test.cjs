const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const ts = require('typescript');

// Exercise the real TypeScript classes with only VS Code, subprocesses and time replaced.
function harness() {
  const terminals = [], requests = [], errors = [], prompts = [], children = [], timers = new Map(), contexts = [];
  let timerId = 0, activeChange;
  const vscode = {
    EventEmitter: class {
      event = () => ({ dispose() {} });
      fire() {}
      dispose() {}
    },
    workspace: { getConfiguration: () => ({ get: () => 'pengupool' }) },
    commands: { executeCommand: async (...args) => { contexts.push(args); } },
    window: {
      terminals,
      onDidOpenTerminal: () => ({ dispose() {} }),
      onDidChangeActiveTerminal: (handler) => { activeChange = handler; return { dispose() {} }; },
      onDidCloseTerminal: () => ({ dispose() {} }),
      createTerminal: (options) => {
        const terminal = {
          name: options.name, options, shows: 0, sent: [], exitStatus: undefined,
          show() { this.shows++; vscode.window.activeTerminal = this; activeChange?.(); },
          sendText(text) { this.sent.push(text); }, dispose() {},
        };
        terminals.push(terminal);
        return terminal;
      },
      showErrorMessage: (message) => errors.push(message),
      showWarningMessage: async () => undefined,
      showInputBox: async (options) => { prompts.push(options); return options.value; },
    },
  };
  const ctl = { runCtl: async (args) => {
    requests.push(args);
    return { code: 0, stdout: JSON.stringify({ command: 'tmux attach', pane: '%9', cwd: '/repo-wt-worker' }), stderr: '' };
  } };
  const spawn = () => {
    const child = new EventEmitter();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = () => { child.emit('close', 0); return true; };
    children.push(child);
    return child;
  };
  function load(name) {
    const filename = path.join(__dirname, '../src', name + '.ts');
    const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2021 },
    }).outputText;
    const exports = {};
    vm.runInNewContext(code, {
      exports, process,
      require: (id) => {
        if (id === 'vscode') return vscode;
        if (id === './util') return ctl;
        // These tests exercise the legacy tmux path (getConfiguration -> 'pengupool' != 'control'),
        // so the control terminal is never constructed; a stub is enough to load terminals.ts.
        if (id === './controlTerminal') return { ControlTerminal: class { showPane() { return Promise.resolve(); } close() {} } };
        if (id === 'child_process') return { spawn };
        throw new Error('Unexpected dependency: ' + id);
      },
      setTimeout: (callback, delay) => { timers.set(++timerId, { callback, delay }); return timerId; },
      clearTimeout: (id) => timers.delete(id),
    }, { filename });
    return exports;
  }
  const { TerminalManager } = load('terminals');
  const manager = new TerminalManager({ workspaceState: { get: () => ({}), update: async () => {} } });
  const { ServeClient } = load('serveClient');
  const client = new ServeClient({ append() {}, appendLine() {} });
  function tick() {
    assert.equal(timers.size, 1, 'one retry scheduled');
    const [id, timer] = [...timers][0];
    timers.delete(id);
    timer.callback();
    return timer.delay;
  }
  return { terminals, requests, errors, prompts, children, timers, contexts, ctl, vscode, manager, client, tick,
    setActive(terminal) { vscode.window.activeTerminal = terminal; activeChange?.(); } };
}

const node = { id: 'sid', name: 'normalized-worker', cwd: '/repo-wt-worker', tmux_pane: '%9', children: [] };

test('Claude terminal shortcut context follows the active terminal', async () => {
  const h = harness();
  await h.manager.newSession('/repo', 'worker', 'cc', false);
  assert.deepEqual(h.contexts.at(-1), ['setContext', 'pengupool.claudeTerminal', true]);
  h.setActive({ name: 'unrelated' });
  assert.deepEqual(h.contexts.at(-1), ['setContext', 'pengupool.claudeTerminal', false]);
});

test('new worktree terminal is reused despite changed cwd and normalized name', async () => {
  const h = harness();
  await h.manager.newSession('/repo', 'normalized worker');
  h.manager.reconcile([node]);
  await h.manager.switchTo(node);
  assert.equal(h.terminals.length, 1);
  assert.equal(h.requests.length, 1);
  assert.equal(h.errors.length, 0);
});

test('snapshot arriving before launch response still binds the terminal', async () => {
  const h = harness();
  h.manager.reconcile([node]);
  await h.manager.newSession('/repo', 'worker');
  await h.manager.switchTo(node);
  assert.equal(h.terminals.length, 1);
});

test('Add Previous focuses an already attached session', async () => {
  const h = harness();
  await h.manager.newSession('/repo', 'worker');
  h.manager.reconcile([node]);
  await h.manager.resume('/repo', 'worker', 'sid');
  assert.equal(h.terminals.length, 1);
  assert.equal(h.requests.length, 1);
});

test('Add Previous uses attach/adopt flow for a live session', async () => {
  const h = harness();
  h.manager.reconcile([node]);
  await h.manager.resume('/repo', 'worker', 'sid');
  assert.equal(h.requests[0][1], 'attach');
});

test('new session passes the harness as the fourth ctl argument', async () => {
  const h = harness();
  await h.manager.newSession('/repo', 'worker', 'pi', false);
  assert.equal(h.requests[0].slice(1, 3).join(' '), 'new /repo');
  assert.equal(h.requests[0].slice(5).join(' '), 'pi folder');
});

test('adoption sends no launch argument after the confirmation', async () => {
  const h = harness();
  h.vscode.window.showWarningMessage = async () => 'Adopt (stop & re-host)';
  h.ctl.runCtl = async (args) => {
    h.requests.push(args);
    return args[1] === 'attach'
      ? { code: 2, stdout: '', stderr: '' }
      : { code: 0, stdout: JSON.stringify({ command: 'tmux attach', pane: '%9', cwd: '/repo-wt-worker' }), stderr: '' };
  };
  assert.equal(await h.manager.switchTo(node), true);
  assert.equal(h.requests.at(-1).length, 4);
  assert.equal(h.requests.at(-1).slice(0, 3).join(' '), '--json adopt sid');
  assert.equal(h.prompts.length, 0);
});

test('a pi session gets its own terminal and view beside the Claude one', async () => {
  const h = harness();
  const pi = { ...node, id: 'pid', name: 'pi-worker', harness: 'pi' };
  h.ctl.runCtl = async (args) => {
    h.requests.push(args);
    const harness = args.includes('pid') ? 'pi' : 'cc';
    return { code: 0, stdout: JSON.stringify({ command: 'tmux attach', pane: '%9', cwd: '/repo', harness }), stderr: '' };
  };
  await h.manager.switchTo(node);
  await h.manager.switchTo(pi);
  await h.manager.switchTo(node);
  assert.equal(h.terminals.map((terminal) => terminal.name).join(','), 'PenguPool,PenguPool · pi');
  assert.equal(h.requests.length, 2, 'returning to the Claude session reuses its terminal');
  assert.notEqual(h.requests[0].at(-1), h.requests[1].at(-1), 'each harness has its own view id');
});

test('a new Claude terminal never binds to a pi session with the same pane id', async () => {
  const h = harness();
  await h.manager.newSession('/repo', 'normalized worker');
  const piTwin = { ...node, id: 'pid', harness: 'pi' };  // %9 on the pi server, same cwd
  h.manager.reconcile([piTwin, node]);
  await h.manager.switchTo(node);
  assert.equal(h.requests.length, 1, 'the Claude terminal already shows the Claude session');
  assert.equal(h.terminals.length, 1);
});

test('failed close keeps the existing terminal', async () => {
  const h = harness();
  await h.manager.switchTo(node);
  h.ctl.runCtl = async () => ({ code: 1, stdout: '', stderr: 'close failed' });
  await h.manager.close(node);
  await h.manager.switchTo(node);
  assert.equal(h.terminals.length, 1);
  assert.equal(h.errors[0], 'PenguPool: close failed');
});

test('default terminal prefers an existing attachment over the first session', async () => {
  const h = harness();
  const first = { ...node, id: 'first', name: 'first' };
  await h.manager.switchTo(node);
  await h.manager.showDefault([first, node]);
  assert.equal(h.terminals.length, 1);
  assert.equal(h.terminals[0].shows, 2);
});

test('switching sessions reuses one terminal and switches its tmux view', async () => {
  const h = harness();
  const other = { ...node, id: 'other', name: 'other', tmux_pane: '%10' };
  await h.manager.switchTo(node);
  await h.manager.switchTo(other);
  assert.equal(h.terminals.length, 1);
  assert.equal(h.requests[1][0], 'select');
});

test('session terminal directly owns tmux and is transient across reloads', async () => {
  const h = harness();
  await h.manager.switchTo(node);
  assert.equal(h.terminals[0].options.isTransient, true);
  assert.equal(h.terminals[0].options.strictEnv, true);
  assert.equal(h.terminals[0].sent.length, 0);
  assert.match(h.terminals[0].options.shellArgs.at(-1), /^exec tmux attach/);
});

test('late-restored legacy session terminals are removed after the first snapshot', () => {
  const h = harness();
  const legacy = {
    name: node.name,
    creationOptions: { name: node.name },
    exitStatus: undefined,
    disposed: false,
    dispose() { this.disposed = true; },
  };
  h.terminals.push(legacy);
  h.manager.reconcile([node]);
  assert.equal(legacy.disposed, true);
});

test('a user terminal named like a session is never closed', () => {
  const h = harness();
  h.manager.reconcile([node]);                        // the one-time legacy sweep is done
  const mine = { name: node.name, creationOptions: {}, exitStatus: undefined, disposed: false, dispose() { this.disposed = true; } };
  h.terminals.push(mine);
  h.manager.reconcile([node]);
  assert.equal(mine.disposed, false);
  const restored = { name: node.name, creationOptions: {}, exitStatus: undefined, disposed: false, dispose() { this.disposed = true; } };
  const fresh = harness();
  fresh.terminals.push(restored);                     // a user shell present at the first snapshot, no explicit name
  fresh.manager.reconcile([node]);
  assert.equal(restored.disposed, false);
});

test('opening the work pane removes an untouched auto-created shell terminal', async () => {
  const h = harness();
  const shell = {
    name: 'zsh',
    state: { isInteractedWith: false },
    exitStatus: undefined,
    disposed: false,
    dispose() { this.disposed = true; },
  };
  h.terminals.push(shell);
  await h.manager.switchTo(node);
  assert.equal(shell.disposed, true);
});

test('asynchronous spawn errors retry once, with increasing backoff', () => {
  const h = harness();
  h.client.start();
  h.children[0].emit('error', Object.assign(new Error('missing CLI'), { code: 'ENOENT' }));
  h.children[0].emit('close', -2);
  assert.equal(h.tick(), 500);
  h.children[1].emit('error', new Error('still missing'));
  h.children[1].emit('close', -2);
  assert.equal(h.tick(), 1000);
  h.children[2].stdout.write('{"rev":1}\n');
  h.children[2].emit('close', 1);
  assert.equal(h.tick(), 500);
  h.client.dispose();
  assert.equal(h.timers.size, 0);
});

test('refresh cancels pending retry and dispose prevents future launches', () => {
  const h = harness();
  h.client.start();
  h.children[0].emit('close', 1);
  h.client.restart();
  assert.equal(h.timers.size, 0);
  assert.equal(h.children.length, 2);
  h.children[1].emit('close', 1);
  h.client.dispose();
  assert.equal(h.timers.size, 0);
});

test('a restarted backend does not inherit a partial snapshot', () => {
  const h = harness();
  h.client.start();
  h.children[0].stdout.write('{"rev":');
  h.children[0].emit('close', 1);
  h.tick();
  h.children[1].stdout.write('{"rev":2}\n');
  assert.equal(h.client.lastSnapshot.rev, 2);
  h.client.dispose();
});
