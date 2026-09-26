const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const { execFileSync } = require('node:child_process');
const ts = require('typescript');

/** Compile a TypeScript source in the current realm (see control-protocol.test.cjs). */
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

function haveTmux() {
  try { execFileSync('tmux', ['-V'], { stdio: 'ignore' }); return true; } catch { return false; }
}

/** node-pty's prebuilt spawn-helper needs the executable bit; npm sometimes drops it. */
function fixHelper() {
  try {
    fs.chmodSync(path.join(__dirname, '../node_modules/node-pty/prebuilds',
      `${process.platform}-${process.arch}`, 'spawn-helper'), 0o755);
  } catch { /* not installed for this platform */ }
}

/** Start a disposable tmux server; null when the sandbox forbids creating a server/socket. */
function disposable(sock) {
  const tmux = (...args) => execFileSync('tmux', ['-L', sock, ...args], { stdio: ['ignore', 'pipe', 'pipe'] }).toString();
  try { execFileSync('tmux', ['-L', sock, 'kill-server'], { stdio: 'ignore' }); } catch { /* none */ }
  try { tmux('-f', '/dev/null', 'new-session', '-d', '-s', 't'); } catch { return null; }
  return tmux;
}

const settle = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(predicate, timeout = 4000) {
  const deadline = Date.now() + timeout;
  while (!predicate() && Date.now() < deadline) { await settle(50); }
}

test('a control session paints a pane, streams live output, and strips mouse modes', async (t) => {
  if (!haveTmux()) { t.skip('tmux is not installed'); return; }
  fixHelper();
  const sock = `pp-cm-${process.pid}`;
  const tmux = disposable(sock);
  if (!tmux) { t.skip('tmux server could not start here'); return; }
  try {
    tmux('send-keys', '-t', 't', "printf 'SEED-\\033[32mGREEN\\033[0m\\n'", 'Enter');
    const pane = tmux('list-panes', '-F', '#{pane_id}').trim().split('\n')[0];

    let out = '';
    const session = new ControlSession({ socket: sock, view: `pv-ext-${process.pid}`, base: 't',
                                         cols: 100, rows: 30, cwd: process.cwd() });
    session.onData = (data) => { out += data; };
    try { session.start(); } catch (error) { t.skip(`node-pty cannot spawn here: ${error.message}`); return; }
    await settle(600);

    await session.show(pane);
    assert.match(out, /SEED-/, 'paints the pane screen captured on attach');
    assert.match(out, /\x1b\[32m/, 'keeps colors from capture-pane');

    out = '';
    session.input('echo LIVE-MARK\r');
    await until(() => out.includes('LIVE-MARK'));
    assert.match(out, /LIVE-MARK/, 'forwards typed input to the pane and streams its output back');

    out = '';
    session.input("printf '\\033[?1000hX\\033[?25l'\r");
    await until(() => out.includes('X'));
    assert.doesNotMatch(out, /\x1b\[\?1000h/, 'drops mouse-tracking so VS Code keeps native selection');
    assert.match(out, /\x1b\[\?25l/, 'keeps unrelated modes (cursor hide)');

    assert.equal(session.running, true);
    session.dispose();
  } finally {
    try { execFileSync('tmux', ['-L', sock, 'kill-server'], { stdio: 'ignore' }); } catch { /* gone */ }
  }
});

test('switching panes re-seeds from the pane that was not visible', async (t) => {
  if (!haveTmux()) { t.skip('tmux is not installed'); return; }
  fixHelper();
  const sock = `pp-cm2-${process.pid}`;
  const tmux = disposable(sock);
  if (!tmux) { t.skip('tmux server could not start here'); return; }
  try {
    const first = tmux('list-panes', '-F', '#{pane_id}').trim().split('\n')[0];
    tmux('new-window', '-d', '-t', 't', '-n', 'other');
    tmux('send-keys', '-t', 'other', 'echo SECOND-PANE-CONTENT', 'Enter');
    await settle(300);

    let out = '';
    const session = new ControlSession({ socket: sock, view: `pv-ext2-${process.pid}`, base: 't',
                                         cols: 100, rows: 30, cwd: process.cwd() });
    session.onData = (data) => { out += data; };
    try { session.start(); } catch (error) { t.skip(`node-pty cannot spawn here: ${error.message}`); return; }
    await settle(500);

    await session.show(first);
    assert.match(out, /%|[$#>]/, 'first pane has a prompt');
    out = '';
    await session.show(first === '%0' ? '%1' : '%0');  // the pane we never streamed
    assert.match(out, /SECOND-PANE-CONTENT/, 'seeds the newly shown pane from capture-pane');
    out = '';
    session.input('echo AFTER-SWITCH\r');
    await until(() => out.includes('AFTER-SWITCH'));
    assert.match(out, /AFTER-SWITCH/, 'live output streams for the newly selected window');
    session.dispose();
  } finally {
    try { execFileSync('tmux', ['-L', sock, 'kill-server'], { stdio: 'ignore' }); } catch { /* gone */ }
  }
});

test('seeds existing scrollback so native scroll and find reach earlier output', async (t) => {
  if (!haveTmux()) { t.skip('tmux is not installed'); return; }
  fixHelper();
  const sock = `pp-cm3-${process.pid}`;
  const tmux = disposable(sock);
  if (!tmux) { t.skip('tmux server could not start here'); return; }
  try {
    // Push HISTORY-START well above the visible screen so only a history capture can see it.
    tmux('send-keys', '-t', 't', "printf 'HISTORY-START\\n'; seq 1 80", 'Enter');
    await settle(400);
    const pane = tmux('list-panes', '-F', '#{pane_id}').trim().split('\n')[0];
    const visible = tmux('capture-pane', '-p', '-t', pane).toString();
    assert.doesNotMatch(visible, /HISTORY-START/, 'the marker has scrolled off the visible screen');

    let out = '';
    const session = new ControlSession({ socket: sock, view: `pv-ext3-${process.pid}`, base: 't',
                                         cols: 100, rows: 30, cwd: process.cwd(), historyLines: 500 });
    session.onData = (data) => { out += data; };
    try { session.start(); } catch (error) { t.skip(`node-pty cannot spawn here: ${error.message}`); return; }
    await settle(500);
    await session.show(pane);
    assert.match(out, /HISTORY-START/, 'earlier scrollback is seeded into the terminal');
    session.dispose();
  } finally {
    try { execFileSync('tmux', ['-L', sock, 'kill-server'], { stdio: 'ignore' }); } catch { /* gone */ }
  }
});

/** Feed `paint` to a real terminal emulator (a tmux pane running a no-echo `cat`) and read back the
 *  cursor it lands on. tmux is the emulator here, so this checks cursor math the way a host would. */
async function emulatedCursor(tmux, paint, cols, rows) {
  tmux('new-window', '-d', '-t', 't:', '-n', 'sink', 'stty raw -echo; cat');
  const sink = tmux('list-panes', '-t', 't:sink', '-F', '#{pane_id}').trim();
  tmux('resize-window', '-t', 't:sink', '-x', String(cols), '-y', String(rows));
  const bytes = Buffer.from(paint, 'utf8');
  for (let i = 0; i < bytes.length; i += 900) {
    const hex = [...bytes.subarray(i, i + 900)].map((b) => b.toString(16).padStart(2, '0'));
    tmux('send-keys', '-t', sink, '-H', ...hex);
  }
  await settle(400);  // let cat echo the bytes and tmux render them
  const cursor = tmux('display-message', '-p', '-t', sink, '#{cursor_x} #{cursor_y}').trim().split(' ').map(Number);
  return cursor;
}

test('seeding history leaves the cursor on the pane cursor, not inside the history', async (t) => {
  if (!haveTmux()) { t.skip('tmux is not installed'); return; }
  fixHelper();
  const sock = `pp-cm4-${process.pid}`;
  const tmux = disposable(sock);
  if (!tmux) { t.skip('tmux server could not start here'); return; }
  try {
    tmux('send-keys', '-t', 't', "seq 1 60; printf 'CURSOR-CHECK\\n'", 'Enter');
    await settle(400);
    const pane = tmux('list-panes', '-F', '#{pane_id}').trim().split('\n')[0];
    const dm = (fmt) => tmux('display-message', '-p', '-t', pane, fmt).trim();
    const [cx, cy] = dm('#{cursor_x} #{cursor_y}').split(' ').map(Number);
    const cols = Number(dm('#{pane_width}'));
    const rows = Number(dm('#{pane_height}'));

    let paint = '';
    const session = new ControlSession({ socket: sock, view: `pv-ext4-${process.pid}`, base: 't',
                                         cols, rows, cwd: process.cwd(), historyLines: 500 });
    session.onData = (data) => { paint += data; };
    try { session.start(); } catch (error) { t.skip(`node-pty cannot spawn here: ${error.message}`); return; }
    await settle(500);
    await session.show(pane);
    assert.match(paint, /CURSOR-CHECK/, 'the visible screen was captured');

    const cursor = await emulatedCursor(tmux, paint, cols, rows);
    assert.deepEqual(cursor, [cx, cy], 'a real emulator lands the cursor on the pane cursor after history');
    session.dispose();
  } finally {
    try { execFileSync('tmux', ['-L', sock, 'kill-server'], { stdio: 'ignore' }); } catch { /* gone */ }
  }
});

test('streams a busy pane without losing or reordering output', async (t) => {
  if (!haveTmux()) { t.skip('tmux is not installed'); return; }
  fixHelper();
  const sock = `pp-cm5-${process.pid}`;
  const tmux = disposable(sock);
  if (!tmux) { t.skip('tmux server could not start here'); return; }
  try {
    // Start a numbered stream, then attach mid-flight so both the capture and live %output are used.
    tmux('send-keys', '-t', 't', 'for i in $(seq 1 200); do echo MARK-$i; sleep 0.004; done', 'Enter');
    await settle(150);
    const pane = tmux('list-panes', '-F', '#{pane_id}').trim().split('\n')[0];
    let out = '';
    const session = new ControlSession({ socket: sock, view: `pv-ext5-${process.pid}`, base: 't',
                                         cols: 100, rows: 30, cwd: process.cwd(), historyLines: 500 });
    session.onData = (data) => { out += data; };
    try { session.start(); } catch (error) { t.skip(`node-pty cannot spawn here: ${error.message}`); return; }
    await settle(300);
    await session.show(pane);
    await settle(2000);  // let the whole loop stream; its echoed command is not a reliable sentinel
    session.dispose();

    const seq = [...out.matchAll(/MARK-(\d+)/g)].map((m) => Number(m[1]));
    const seen = new Set(seq);
    const missing = []; for (let i = 1; i <= 200; i++) { if (!seen.has(i)) { missing.push(i); } }
    let inversions = 0; for (let i = 1; i < seq.length; i++) { if (seq[i] < seq[i - 1]) { inversions++; } }
    assert.deepEqual(missing, [], 'no output line is dropped while seeding and then streaming');
    assert.equal(inversions, 0, 'output is never reordered or replayed');
  } finally {
    try { execFileSync('tmux', ['-L', sock, 'kill-server'], { stdio: 'ignore' }); } catch { /* gone */ }
  }
});
