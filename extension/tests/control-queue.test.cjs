const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

function load(file, deps = {}) {
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

const protocol = load('controlProtocol.ts');
const { ControlSession } = load('controlSession.ts', { './controlProtocol': protocol });

/** A node-pty stand-in the test drives directly: it records commands and can emit frames. */
class FakePty {
  constructor() { this.data = []; this.exitHandlers = []; this.written = []; this.autoAnswer = true; }
  onData(cb) { this.data.push(cb); }
  onExit(cb) { this.exitHandlers.push(cb); }
  write(text) {
    this.written.push(text);
    if (this.onWrite) { this.onWrite(text); }
    else if (this.autoAnswer) { queueMicrotask(() => this.emit('%end 1 0 0\r\n')); }
  }
  resize() {}
  kill() { this.killed = true; }
  emit(text) { for (const cb of this.data) { cb(Buffer.from(text, 'utf8')); } }
}

const settle = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** A started session whose terminal is showing %0 (auto-answered), ready for input tests. */
async function open(timeoutMs) {
  const pty = new FakePty();
  const control = new ControlSession({ socket: 's', view: 'v', base: 't', cols: 80, rows: 24,
                                       spawn: () => pty, commandTimeoutMs: timeoutMs });
  control.start();
  pty.emit('\x1bP1000p%begin 1 1 0\r\n%end 1 1 0\r\n');  // attach handshake
  await control.show('%0');
  pty.autoAnswer = false;
  return { control, pty };
}

test('a timed-out command advances the queue so input keeps flowing', async () => {
  const { control, pty } = await open(40);
  const base = pty.written.length;
  control.input('a');
  control.input('b');
  await settle(10);
  assert.equal(pty.written.length - base, 1, 'first keystroke command sent');
  pty.emit('%begin 2 2 0\r\n');  // its block opens, then it times out

  await settle(80);  // no %end arrives -> the command times out
  assert.equal(pty.written.length - base, 2, 'the queue advanced to the next keystroke');
  control.dispose();
});

test('a command that never answers closes the session instead of mis-matching a later reply', async () => {
  const { control, pty } = await open(40);
  control.input('a');  // no %begin ever arrives; a later block could not be told apart from this reply
  await settle(80);
  assert.equal(control.running, false, 'the session is closed rather than left desynchronised');
  assert.equal(pty.killed, true);
});

test('a late reply to a timed-out command does not resolve the next command', async () => {
  const { control, pty } = await open(40);
  const base = pty.written.length;
  control.input('a');
  await settle(10);
  pty.emit('%begin 2 2 0\r\n');   // the first command's block opens, then it times out
  await settle(80);
  pty.emit('%end 2 2 0\r\n');     // late reply for the abandoned block

  control.input('b');
  await settle(10);
  const afterB = pty.written.length;
  pty.emit('%begin 3 3 0\r\n%end 3 3 0\r\n');  // complete b's block
  await settle(10);
  assert.equal(pty.written.length, afterB, 'stale %end did not desync the queue');
  assert.equal(afterB - base, 2, 'b was sent after the timeout');
  control.dispose();
});

test('responses are matched by block id, not arrival order', async () => {
  const { control, pty } = await open(1000);
  const base = pty.written.length;
  control.input('a');
  await settle(10);
  pty.emit('%begin 2 77 0\r\n');               // the in-flight command's block id is 77
  pty.emit('%begin 3 78 0\r\n%end 3 78 0\r\n'); // a foreign block must not release the queue
  control.input('b');
  await settle(10);
  assert.equal(pty.written.length - base, 1, 'the second command waits behind the first');
  pty.emit('%end 2 77 0\r\n');                 // the matching %end releases the queue
  await settle(10);
  assert.equal(pty.written.length - base, 2, 'the matching %end released the next command');
  control.dispose();
});

test('seeding keeps output produced after the capture and never replays it twice', async () => {
  const pty = new FakePty();
  pty.autoAnswer = false;
  const control = new ControlSession({ socket: 's', view: 'v', base: 't', cols: 80, rows: 24,
                                       spawn: () => pty, commandTimeoutMs: 1000, historyLines: 50 });
  control.start();
  pty.emit('\x1bP1000p%begin 1 1 0\r\n%end 1 1 0\r\n');
  let painted = '';
  control.onData = (data) => { painted += data; };
  // tmux emits %output in order with the capture reply: bytes before the reply are already in the
  // capture; bytes after it are new.
  pty.onWrite = (cmd) => {
    if (cmd.startsWith('select-window')) { pty.emit('%begin 2 2 0\r\n%end 2 2 0\r\n'); }
    else if (cmd.startsWith('capture-pane')) {
      pty.emit('%output %0 OLD\015\012');
      pty.emit('%begin 3 3 0\r\nOLD\r\n%end 3 3 0\r\n');
      pty.emit('%output %0 NEW\015\012');
    } else { pty.emit('%begin 4 4 0\r\n%end 4 4 0\r\n'); }
  };
  await control.show('%0');
  pty.onWrite = undefined;
  pty.emit('%output %0 LIVE\015\012');  // normal streaming once seeded
  await settle(20);

  assert.equal((painted.match(/OLD/g) || []).length, 1, 'captured text is painted once, not replayed');
  assert.equal((painted.match(/NEW/g) || []).length, 1, 'output after the reply is kept exactly once');
  assert.match(painted, /LIVE/, 'live streaming resumes after seeding');
  control.dispose();
});

test('input during a pending switch targets the pane being switched to', async () => {
  const { control, pty } = await open(1000);   // currently showing %0
  const base = pty.written.length;
  const pending = control.show('%1');           // requested, not yet painted
  control.input('Z');                           // typed immediately after the switch request
  pty.autoAnswer = true;                        // let the queued show commands drain
  await pending;
  await settle(20);
  const sends = pty.written.slice(base).filter((cmd) => cmd.startsWith('send-keys'));
  assert.ok(sends.length >= 1, 'the keystroke was sent');
  assert.ok(sends.every((cmd) => cmd.includes('-t %1')), 'input goes to the requested pane, not the old one');
  control.dispose();
});
