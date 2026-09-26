const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');

/** Compile a TypeScript source in the current realm so its objects compare with deepStrictEqual. */
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

const { ControlParser, unescapeOutput, parseControlLine, hexKeys, MouseModeFilter } = load('controlProtocol.ts');

test('unescapes tmux octal output, including backslash as \\134', () => {
  // Real tmux escapes CR/LF/ESC as octal and a literal backslash as \134 (0x5c).
  assert.deepEqual([...unescapeOutput('a\\015b\\012\\033[31m')], [0x61, 0x0d, 0x62, 0x0a, 0x1b, 0x5b, 0x33, 0x31, 0x6d]);
  assert.deepEqual([...unescapeOutput('back\\134slash')], [...Buffer.from('back\\slash', 'utf8')]);
  assert.deepEqual([...unescapeOutput('plain')], [...Buffer.from('plain', 'utf8')]);
  assert.deepEqual([...unescapeOutput('caf\u00e9')], [...Buffer.from('caf\u00e9', 'utf8')]);  // literal UTF-8 passes through
});

test('parses the notification kinds tmux actually sends', () => {
  const out = parseControlLine('%output %3 hello\\015');
  assert.equal(out.kind, 'output');
  assert.equal(out.pane, '%3');
  assert.deepEqual([...out.data], [...Buffer.from('hello\r')]);

  assert.deepEqual(parseControlLine('%begin 1790427654 282 0'), { kind: 'begin', id: 282, flags: 0 });
  assert.deepEqual(parseControlLine('%end 1790427654 287 1'), { kind: 'end', id: 287, flags: 1 });
  assert.deepEqual(parseControlLine('%error 1790427654 288 0'), { kind: 'error', id: 288, flags: 0 });
  assert.deepEqual(parseControlLine('%window-renamed @0 renamed'),
                   { kind: 'notify', name: 'window-renamed', args: ['@0', 'renamed'] });
  assert.deepEqual(parseControlLine('%layout-change @0 a87d,100x30,0,0,0 a87d,100x30,0,0,0 *'),
                   { kind: 'notify', name: 'layout-change',
                     args: ['@0', 'a87d,100x30,0,0,0', 'a87d,100x30,0,0,0', '*'] });
  assert.deepEqual(parseControlLine('plain response text'), { kind: 'block', line: 'plain response text' });
});

test('splits a stream arriving in arbitrary chunks and strips the DCS introducer', () => {
  const parser = new ControlParser();
  // The first line carries tmux's control-mode DCS prefix; the pane payload is split mid-sequence.
  const frames = parser.push(Buffer.from('\x1bP1000p%session-changed $0 t\r\n%output %1 ab\\0'));
  assert.deepEqual(frames, [{ kind: 'notify', name: 'session-changed', args: ['$0', 't'] }]);
  const more = parser.push(Buffer.from('15cd\r\n%output %1 e\r\n'));
  assert.equal(more.length, 2);
  assert.deepEqual([...more[0].data], [...Buffer.from('ab\rcd', 'utf8')]);  // \015 is CR
  assert.deepEqual([...more[1].data], [...Buffer.from('e', 'utf8')]);
});

test('a multi-byte character split across chunks is reassembled', () => {
  const parser = new ControlParser();
  const line = Buffer.from('%output %0 caf\u00e9\r\n', 'utf8');
  const cut = line.indexOf(Buffer.from('\u00e9', 'utf8')[0]);
  const first = parser.push(line.subarray(0, cut + 1));
  const second = parser.push(line.subarray(cut + 1));
  assert.deepEqual(first, []);
  assert.equal(second.length, 1);
  assert.equal(second[0].data.toString('utf8'), 'caf\u00e9');
});

test('a command response keeps %-lines and blank rows verbatim', () => {
  // Pane text can contain lines starting with `%` (even resembling `%end`) and blank rows; inside a
  // response block only the terminator with the block's id closes it.
  const parser = new ControlParser();
  const frames = parser.push(Buffer.from(
    '%begin 1 5 0\r\nalpha\r\n\r\n%end 9 9 9\r\n%output %0 x\r\nbeta\r\n%end 1 5 0\r\n%window-renamed @0 w\r\n'));
  assert.deepEqual(frames.map((f) => f.kind), ['begin', 'block', 'block', 'block', 'block', 'block', 'end', 'notify']);
  assert.deepEqual(frames.filter((f) => f.kind === 'block').map((f) => f.line),
                   ['alpha', '', '%end 9 9 9', '%output %0 x', 'beta']);
  assert.deepEqual(frames.at(-1), { kind: 'notify', name: 'window-renamed', args: ['@0', 'w'] });
});

test('hexKeys encodes bytes for tmux send-keys -H', () => {
  assert.equal(hexKeys(Buffer.from([0x41, 0x0a, 0xff])), '41 0a ff');
  assert.equal(hexKeys(Buffer.from('')), '');
});

test('mouse-tracking modes are stripped so the host terminal keeps selection', () => {
  const filter = new MouseModeFilter();
  assert.equal(filter.push('\x1b[?1000;25h'), '\x1b[?25h', 'keep unrelated modes from a mixed sequence');
  assert.equal(filter.push('\x1b[?25;1000;1006h'), '\x1b[?25h');
  assert.equal(filter.push('\x1b[?1000h\x1b[?1006h'), '');
  assert.equal(filter.push('\x1b[?1002;1006h'), '');
  assert.equal(filter.push('\x1b[?1049h'), '\x1b[?1049h');  // alt screen is not mouse mode
  assert.equal(filter.push('plain text'), 'plain text');
  assert.equal(filter.push('\x1b[?1004h\x1b[?25l'), '\x1b[?25l');  // cursor hide kept
});

test('mouse-mode sequences split across chunks are still stripped', () => {
  const filter = new MouseModeFilter();
  assert.equal(filter.push('\x1b[?10'), '');
  assert.equal(filter.push('00h'), '');
  assert.equal(filter.push('\x1b[?100'), '');
  const rest = filter.push('6hvisible');
  assert.equal(rest, 'visible');
});
