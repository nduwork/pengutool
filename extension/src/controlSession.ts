/**
 * One `tmux -CC` control-mode client: PenguPool's replacement for running a real `tmux attach` in a
 * VS Code terminal.
 *
 * Why: in normal attach mode tmux owns the screen and the mouse, so VS Code's native selection,
 * copy, scroll, and find stop working. In control mode tmux streams pane output to us (`%output`)
 * and takes input commands; VS Code renders the pane itself, so selection and scrolling are native.
 * tmux still owns the session, so the work survives closing the editor.
 *
 * Rendering model: on attach/switch we ask tmux for the pane's current screen (`capture-pane -e`),
 * paint that once, then stream live `%output` bytes. Mouse-tracking sequences are stripped so the
 * host terminal keeps native selection. Input goes back as raw bytes via `send-keys -H`.
 *
 * Framing: tmux answers each command with exactly one `%begin`/`%end` block, so every command —
 * including keystrokes — goes through one serialized queue (`command`). Blocks that arrive before
 * the client is ready (`ready`) are the attach handshake and are never mistaken for a response.
 *
 * Deliberately free of `vscode` so it can be exercised against a real tmux in tests.
 */
import * as pty from 'node-pty';
import { StringDecoder } from 'node:string_decoder';
import { ControlParser, ControlFrame, hexKeys, MouseModeFilter } from './controlProtocol';

export interface ControlSessionOptions {
  socket: string;  // tmux `-L` socket (one per harness: "pengupool" / "pengupool-pi")
  view: string;    // grouped view session this client owns, so it never disturbs other clients
  base: string;    // session to group the view from ("pengupool")
  cwd?: string;
  cols: number;
  rows: number;
  /** Injection point for tests; defaults to node-pty's spawn. */
  spawn?: typeof pty.spawn;
  /** Response timeout for a tmux command, ms; defaults to 4000. */
  commandTimeoutMs?: number;
  /** Lines of pane scrollback to seed on attach/switch, so native scroll and find reach earlier
   *  output; 0 seeds only the visible screen. Defaults to 1000. */
  historyLines?: number;
}

const COMMAND_TIMEOUT_MS = 4000;
const READY_FALLBACK_MS = 600;  // send commands even if tmux never emits the attach handshake
const INPUT_CHUNK = 1024;       // bytes per `send-keys -H`; keeps the command line short

export class ControlSession {
  /** Called with rendered bytes for the visible pane, ready to write to the terminal. */
  onData?: (data: string) => void;
  /** Called once the tmux client exits (detach, session gone, or tmux not found). */
  onExit?: (code: number) => void;

  private proc?: pty.IPty;
  private parser = new ControlParser();
  private decoder = new StringDecoder('utf8');
  private filter = new MouseModeFilter();
  private pane = '';    // pane whose output we forward / seed (set once the switch paints)
  private target = '';  // pane input goes to (set as soon as a switch is requested)
  private cols: number;
  private rows: number;
  private inflight: { id?: number; lines: string[]; resolve: (text: string) => void; onReply?: () => void } | null = null;
  private readonly staleEnds = new Set<number>();  // blocks abandoned by a timeout, to be ignored
  private queue: Array<() => void> = [];
  private started = false;
  private exited = false;
  private blockOpen = false;
  private readyResolved = false;
  private readonly markReady: () => void;
  private readonly ready: Promise<void>;
  private seeding = false;
  private seedBuffer = '';
  private showChain: Promise<void> = Promise.resolve();
  private generation = 0;
  private readonly timeout: number;
  private readonly historyLines: number;

  constructor(private readonly opts: ControlSessionOptions) {
    this.cols = opts.cols;
    this.rows = opts.rows;
    this.timeout = opts.commandTimeoutMs ?? COMMAND_TIMEOUT_MS;
    this.historyLines = Math.max(0, opts.historyLines ?? 1000);
    let resolve!: () => void;
    this.ready = new Promise<void>((done) => { resolve = done; });
    this.markReady = () => { if (!this.readyResolved) { this.readyResolved = true; resolve(); } };
  }

  get running(): boolean { return this.started && !this.exited; }

  start(): void {
    if (this.started) { return; }
    this.started = true;
    const args = ['-CC', '-L', this.opts.socket, 'new-session', '-A', '-s', this.opts.view, '-t', this.opts.base];
    // `encoding: null` yields raw Buffers, so a multi-byte character split across tmux's writes is
    // decoded by the streaming StringDecoder instead of being mangled by node-pty.
    this.proc = (this.opts.spawn ?? pty.spawn)('tmux', args, {
      name: 'tmux-256color',
      cols: this.cols,
      rows: this.rows,
      cwd: this.opts.cwd || process.cwd(),
      env: { ...process.env, TERM: 'tmux-256color' } as { [key: string]: string },
      encoding: null as unknown as string,
    });
    this.proc.onData((chunk) => this.consume(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as string, 'utf8')));
    this.proc.onExit(({ exitCode }) => { this.exited = true; this.markReady(); this.onExit?.(exitCode); });
    // The handshake block is not guaranteed on every tmux build; do not stall input forever.
    setTimeout(() => this.markReady(), READY_FALLBACK_MS);
  }

  /** Paint `pane` and stream its live output until another pane is shown. Serialized so overlapping
   *  switches cannot interleave; `force` re-paints a pane whose process was restarted. */
  show(pane: string, force = false): Promise<void> {
    this.target = pane;  // route input here immediately; the paint may still be queued
    const generation = ++this.generation;
    const run = this.showChain.then(() => this.doShow(pane, force, generation));
    this.showChain = run.catch(() => undefined);
    return run;
  }

  /** Re-paint the current pane even if it has not changed. */
  repaint(pane: string): Promise<void> { return this.show(pane, true); }

  /** Send typed input to the visible pane as raw bytes (escape sequences included). Keystrokes share
   *  the command queue so each `%end` still maps to the command that produced it. */
  input(data: string): void {
    if (!this.target || !this.proc || this.exited || !data) { return; }
    const bytes = Buffer.from(data, 'utf8');
    for (let i = 0; i < bytes.length; i += INPUT_CHUNK) {
      void this.command(`send-keys -t ${this.target} -H ${hexKeys(bytes.subarray(i, i + INPUT_CHUNK))}`);
    }
  }

  resize(cols: number, rows: number): void {
    this.cols = cols;
    this.rows = rows;
    if (this.proc && !this.exited) { this.proc.resize(cols, rows); }
  }

  dispose(): void {
    this.exited = true;
    this.markReady();
    try { this.proc?.kill(); } catch { /* already gone */ }
  }

  private async doShow(pane: string, force: boolean, generation: number): Promise<void> {
    if (generation !== this.generation || !this.proc || this.exited) { return; }  // superseded before start
    if (!force && pane === this.pane) { return; }
    this.decoder = new StringDecoder('utf8');
    this.filter = new MouseModeFilter();
    this.seeding = true;
    this.seedBuffer = '';
    this.pane = pane;
    try {
      // Resolve the pane's window and select it in *our* view session: `select-window -t %pane` targets
      // the session that owns the pane, so this grouped view would stay on its old window and tmux would
      // stop streaming %output for the requested pane.
      const win = (await this.command(`display-message -p -t ${pane} '#{window_id}'`)).trim();
      if (win) { await this.command(`select-window -t ${this.opts.view}:${win}`); }
      const alt = (await this.command(`display-message -p -t ${pane} '#{alternate_on}'`)).trim();
      // Seed scrollback only for a normal-buffer pane: a full-screen (alternate) app owns its screen and
      // has no history to give, and writing some would land in the terminal's alternate buffer, which
      // VS Code keeps no scrollback for. Clearing the buffer on the reply (not after the await) drops
      // only the bytes tmux already put in the capture, keeping any that arrive afterwards.
      const history = this.historyLines > 0 && alt !== '1' ? `-S -${this.historyLines} ` : '';
      const text = await this.command(`capture-pane -e -p ${history}-t ${pane}`, () => { this.seedBuffer = ''; });
      const cursor = (await this.command(`display-message -p -t ${pane} '#{cursor_x} #{cursor_y}'`)).trim();
      if (generation !== this.generation) { return; }  // a newer switch took over; let it paint
      let paint = alt === '1' ? '\x1b[?1049h' : '\x1b[?1049l';
      // 3J clears saved lines so one reused terminal does not stack every session's history together.
      paint += '\x1b[3J\x1b[2J\x1b[H' + text.replace(/\n$/, '').split('\n').join('\r\n');
      // CUP is viewport-relative: once the captured history is written the viewport top is the pane's
      // visible screen, so tmux's cursor_y (relative to the visible screen) maps directly. The
      // `emulatedCursor` test renders this paint in a real tmux pane and checks the landed cursor.
      const [x, y] = cursor.split(/\s+/).map(Number);
      if (Number.isFinite(x) && Number.isFinite(y)) { paint += `\x1b[${y + 1};${x + 1}H`; }
      paint += this.seedBuffer;  // bytes that arrived while we were capturing
      this.onData?.(paint);
    } finally {
      this.seeding = false;
      this.seedBuffer = '';
    }
  }

  private raw(cmd: string): void {
    try { this.proc?.write(cmd + '\n'); } catch { /* process gone; exit handler runs */ }
  }

  /** Run a tmux command and resolve with its response block ('' on error or timeout). Every send
   *  awaits `ready` and joins one serialized queue, so responses match commands one-to-one. A
   *  timed-out command is dropped and its late block ignored, so the queue never stalls. */
  private async command(cmd: string, onReply?: () => void): Promise<string> {
    await this.ready;
    return new Promise((resolve) => {
      let settled = false;
      let timer: ReturnType<typeof setTimeout> | undefined;
      const entry: { id?: number; lines: string[]; resolve: (text: string) => void; onReply?: () => void } = {
        lines: [], onReply,
        resolve: (text) => { if (!settled) { settled = true; clearTimeout(timer); resolve(text); } },
      };
      // The timeout starts only when this command is actually sent: a large paste queues many
      // commands, and one must not expire while still waiting behind the others.
      this.queue.push(() => {
        this.inflight = entry;
        this.raw(cmd);
        timer = setTimeout(() => {
          if (this.inflight === entry) {
            if (entry.id !== undefined) {
              // The block is open: remember it so its late %end is ignored, and run the next command.
              this.staleEnds.add(entry.id);
              this.inflight = null;
              this.queue.shift()?.();
            } else {
              // No %begin within the timeout: tmux is not answering, and a later block could be this
              // reply or the next command's, which we cannot tell apart. Close rather than mis-match.
              this.inflight = null;
              entry.resolve(entry.lines.join('\n'));
              this.dispose();
              return;
            }
          }
          entry.resolve(entry.lines.join('\n'));
        }, this.timeout);
      });
      if (!this.inflight) { this.queue.shift()?.(); }
    });
  }

  private consume(chunk: Buffer): void {
    for (const frame of this.parser.push(chunk)) { this.onFrame(frame); }
  }

  private onFrame(frame: ControlFrame): void {
    switch (frame.kind) {
      case 'output':
        this.onPaneOutput(frame.pane, frame.data);
        break;
      case 'begin':
        this.blockOpen = true;
        // Record which block this response belongs to, so a stray or late block cannot resolve it.
        if (this.inflight && this.inflight.id === undefined) { this.inflight.id = frame.id; }
        break;
      case 'block':
        this.inflight?.lines.push(frame.line);
        break;
      case 'end':
      case 'error': {
        this.blockOpen = false;
        if (frame.id !== 0 && this.staleEnds.delete(frame.id)) { break; }  // late reply to a timed-out command
        const done = this.inflight;
        if (done && (done.id === undefined || done.id === frame.id)) {
          this.inflight = null;
          done.onReply?.();
          done.resolve(done.lines.join('\n'));
          this.queue.shift()?.();
        } else if (!done) {
          this.markReady();  // the unsolicited attach handshake completed
        }
        break;
      }
      case 'notify':
        if (frame.name === 'exit') { this.exited = true; this.markReady(); this.onExit?.(0); }
        else if (frame.name === 'session-changed' && !this.blockOpen) { this.markReady(); }
        break;
      default:
        break;
    }
  }

  private onPaneOutput(pane: string, data: Buffer): void {
    if (pane !== this.pane) { return; }  // other panes are re-seeded with capture-pane on switch
    const text = this.filter.push(this.decoder.write(data));
    if (!text) { return; }
    if (this.seeding) { this.seedBuffer += text; } else { this.onData?.(text); }
  }
}
