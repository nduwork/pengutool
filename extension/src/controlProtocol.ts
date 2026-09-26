/**
 * Parser for tmux control-mode (`tmux -CC`) protocol frames.
 *
 * Control mode is line oriented: tmux writes `%`-prefixed notifications and command results as
 * `%begin`/`%end` (or `%error`) blocks, terminated by CRLF. Pane output arrives as
 * `%output %<pane> <payload>`, where the payload is the pane's raw bytes with every byte outside
 * 0x20..0x7e (and backslash) escaped as a three-digit octal `\ooo` (tmux uses `\134` for `\`).
 *
 * This module is deliberately free of `vscode`/`node-pty` so it can be unit-tested on captured
 * fixtures; `controlSession.ts` drives the process around it.
 */

/** One parsed line of the control-mode stream. */
export type ControlFrame =
  | { kind: 'output'; pane: string; data: Buffer }
  | { kind: 'begin'; id: number; flags: number }
  | { kind: 'end'; id: number; flags: number }
  | { kind: 'error'; id: number; flags: number }
  | { kind: 'block'; line: string }
  | { kind: 'notify'; name: string; args: string[] };

/** Decode the escaped body of a `%output` line back into the pane's raw bytes. */
export function unescapeOutput(payload: string): Buffer {
  const parts: Buffer[] = [];
  let literal = '';
  for (let i = 0; i < payload.length; i++) {
    const ch = payload[i];
    if (ch === '\\' && /^[0-7]{3}$/.test(payload.slice(i + 1, i + 4))) {
      if (literal) { parts.push(Buffer.from(literal, 'utf8')); literal = ''; }
      parts.push(Buffer.from([parseInt(payload.slice(i + 1, i + 4), 8) & 0xff]));
      i += 3;
    } else {
      literal += ch;
    }
  }
  if (literal) { parts.push(Buffer.from(literal, 'utf8')); }
  return Buffer.concat(parts);
}

/** Split one control-mode notification line into its name and arguments. */
export function parseControlLine(line: string): ControlFrame | null {
  if (line[0] !== '%') { return { kind: 'block', line }; }
  const space = line.indexOf(' ');
  const name = space === -1 ? line.slice(1) : line.slice(1, space);
  const rest = space === -1 ? '' : line.slice(space + 1);
  if (name === 'output') {
    // `%output %<pane-id> <payload>`; the pane id and payload are separated by a single space.
    const match = /^(%\d+) ?(.*)$/s.exec(rest);
    if (!match) { return null; }
    return { kind: 'output', pane: match[1], data: unescapeOutput(match[2]) };
  }
  if (name === 'begin' || name === 'end' || name === 'error') {
    const parts = rest.split(' ');
    return { kind: name, id: Number.parseInt(parts[1] ?? '0', 10) || 0,
             flags: Number.parseInt(parts[2] ?? '0', 10) || 0 };
  }
  return { kind: 'notify', name, args: rest ? rest.split(' ') : [] };
}

/** Incremental line splitter: feed it arbitrary chunks, get frames. Handles a `%output` line or a
 *  multi-byte character arriving split across chunks, and strips the DCS control-mode introducer. */
export class ControlParser {
  private buffered: Buffer = Buffer.alloc(0);
  private inBlock = false;
  private blockId = 0;

  push(chunk: Buffer): ControlFrame[] {
    this.buffered = this.buffered.length ? Buffer.concat([this.buffered, chunk]) : chunk;
    const frames: ControlFrame[] = [];
    let start = 0;
    for (let nl = this.buffered.indexOf(0x0a, start); nl !== -1; nl = this.buffered.indexOf(0x0a, start)) {
      let line = this.buffered.subarray(start, nl).toString('utf8');
      start = nl + 1;
      if (line.endsWith('\r')) { line = line.slice(0, -1); }
      line = line.replace(/\x1bP1000p/g, '');  // tmux's "you are in control mode" DCS introducer
      if (this.inBlock) {
        // A command response is verbatim text: a pane line may start with `%` (even look like `%end`),
        // and blank rows are real screen rows. Only the terminator with this block's id closes it.
        const term = /^%(end|error) (\d+) (\d+) (\d+)$/.exec(line);
        if (term && Number(term[3]) === this.blockId) {
          this.inBlock = false;
          frames.push({ kind: term[1] as 'end' | 'error', id: Number(term[3]), flags: Number(term[4]) });
        } else {
          frames.push({ kind: 'block', line });
        }
        continue;
      }
      if (!line) { continue; }
      const frame = parseControlLine(line);
      if (frame) {
        if (frame.kind === 'begin') { this.inBlock = true; this.blockId = frame.id; }
        frames.push(frame);
      }
    }
    this.buffered = this.buffered.subarray(start);
    return frames;
  }
}

/** Encode bytes as the space-separated hex pairs `tmux send-keys -H` expects. */
export function hexKeys(data: Buffer): string {
  return [...data].map((byte) => byte.toString(16).padStart(2, '0')).join(' ');
}

// Mouse-tracking and other modes a pane may enable. PenguPool deliberately does not forward them:
// dropping the sequence keeps the host terminal in charge of the mouse, which is what makes
// selection, copy, and wheel scrolling native again.
const STRIPPED_MODES = /^\?10(0[0-6]|15|16|17)$/;
const MODE_SEQUENCE = /\x1b\[\?([0-9;]+)([hl])/g;

/** Remove mouse-tracking mode changes from pane output, carrying a partial escape sequence between
 *  chunks so a sequence split across `%output` frames is still removed. */
export class MouseModeFilter {
  private carry = '';

  push(text: string): string {
    const combined = this.carry + text;
    // Keep a trailing partial escape (up to a full CSI) to finish it next chunk.
    const tail = /(\x1b(?:\[[0-9;?]*)?)$/.exec(combined);
    const head = tail ? combined.slice(0, tail.index) : combined;
    this.carry = tail ? tail[1] : '';
    return head.replace(MODE_SEQUENCE, (whole, params: string, set: string) => {
      const parts = params.split(';');
      const kept = parts.filter((p) => !STRIPPED_MODES.test('?' + p));
      if (kept.length === parts.length) { return whole; }  // no mouse parameters
      if (kept.length === 0) { return ''; }                // nothing but mouse parameters
      return `\x1b[?${kept.join(';')}${set}`;              // keep the unrelated modes
    });
  }
}
