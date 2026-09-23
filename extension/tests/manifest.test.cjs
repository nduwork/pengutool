const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');

const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, '../package.json'), 'utf8'));

test('Shift+Enter uses Claude terminal setup sequence', () => {
  const binding = manifest.contributes.keybindings.find((item) => item.key === 'shift+enter');
  assert.equal(binding.command, 'workbench.action.terminal.sendSequence');
  assert.equal(binding.args.text, '\x1b\r');
  assert.equal(binding.when, 'terminalFocus && pengupool.claudeTerminal');
});

test('terminal shortcuts and settings do not change unrelated terminals', () => {
  assert.equal(manifest.contributes.configurationDefaults, undefined);
  const terminals = fs.readFileSync(path.join(__dirname, '../src/terminals.ts'), 'utf8');
  assert.match(terminals, /onDidChangeActiveTerminal/);
  assert.match(terminals, /setContext', 'pengupool.claudeTerminal'/);
});

test('extension package carries the repository MIT license', () => {
  const root = fs.readFileSync(path.join(__dirname, '../../LICENSE'), 'utf8');
  const extension = fs.readFileSync(path.join(__dirname, '../LICENSE'), 'utf8');
  assert.equal(extension, root);
});

test('Sessions uses a webview so its background can own a context menu', () => {
  const view = manifest.contributes.views.pengupool.find((item) => item.id === 'pengupoolSessions');
  assert.equal(view.type, 'webview');
});

test('pi Sessions view sits between Sessions and Shortcuts, only while both harnesses run', () => {
  const views = manifest.contributes.views.pengupool;
  assert.deepEqual(views.map((item) => item.id), ['pengupoolSessions', 'pengupoolPiSessions', 'pengupoolHints']);
  const pi = views[1];
  assert.equal(pi.name, 'Pi Sessions');
  assert.equal(pi.type, 'webview');
  assert.equal(pi.when, 'pengupool.bothHarnesses');
});

test('Sessions title actions and shortcuts also apply to the pi Sessions view', () => {
  for (const item of manifest.contributes.menus['view/title']) {
    assert.match(item.when, /view == pengupoolPiSessions/);
  }
  for (const item of manifest.contributes.keybindings.filter((binding) => /pengupoolSessions/.test(binding.when))) {
    assert.match(item.when, /focusedView == pengupoolPiSessions/);
  }
});

test('shortcuts avoid a Terminal section and describe PenguPool-specific behavior once', () => {
  const hints = fs.readFileSync(path.join(__dirname, '../src/hintsView.ts'), 'utf8');
  assert.doesNotMatch(hints, /label: 'Terminal'/);
  assert.match(hints, /folder or new worktree · Claude Code or pi/);
  assert.match(hints, /prompt newline in Claude terminal/);
  assert.match(hints, /map cards refresh phase chain · context use/);
});

test('new session picks a harness instead of a free-text launch command', () => {
  const commands = fs.readFileSync(path.join(__dirname, '../src/commands.ts'), 'utf8');
  assert.doesNotMatch(commands, /launchCommand/);
  assert.match(commands, /cc: 'Claude Code', pi: 'pi'/);
  assert.match(commands, /label: 'Create a worktree'/);
  assert.match(commands, /label: 'Use selected folder'/);
});
