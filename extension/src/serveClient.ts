import * as vscode from 'vscode';
import { spawn, ChildProcessWithoutNullStreams } from 'child_process';

/** Agent CLI hosting a session. Each harness has its own tmux server; they never share a tree. */
export type Harness = 'cc' | 'pi';

/** One session node, mirroring pengupool/serve.py `_node`. */
export interface SessionNode {
  id: string;
  name: string;
  cwd: string;
  repo: string;
  pid: number;
  state: 'active' | 'waiting' | 'stale' | 'blocked' | string;
  status: string;
  label: string;
  ctx_pct: number | null;
  started: number;
  tmux_pane: string;   // "" unless the session is hosted in a tmux pane (started by PenguPool)
  harness?: Harness;   // absent from older backends, meaning "cc"
  summary?: string;    // one-line role from the session's profile ('' or absent = role not set)
  children: SessionNode[];
}

/** One `pengupool serve` line. `topo_hash` covers structure only — unchanged hash => restyle in
 *  place, no relayout (see serve.py). */
export interface Snapshot {
  rev: number;
  ts: number;
  topo_hash: string;
  roots: SessionNode[];
  cross: [string, string, string][];
  msgs: [string, string, string, string, boolean][];
}

/**
 * Spawns `<pengupool> serve` and emits a Snapshot per NDJSON line. Restarts with backoff if the
 * backend exits. This is the single bridge to the shared Python model — the extension renders only.
 */
export class ServeClient implements vscode.Disposable {
  private readonly _onSnapshot = new vscode.EventEmitter<Snapshot>();
  readonly onSnapshot = this._onSnapshot.event;
  private readonly _onSpawnError = new vscode.EventEmitter<Error>();
  readonly onSpawnError = this._onSpawnError.event;

  private proc?: ChildProcessWithoutNullStreams;
  private buf = '';
  private stopped = false;
  private backoff = 500;
  private retry?: ReturnType<typeof setTimeout>;
  private _last?: Snapshot;

  constructor(private readonly output: vscode.OutputChannel) {}

  get lastSnapshot(): Snapshot | undefined { return this._last; }

  start(): void {
    this.stopped = false;
    this.spawnOnce();
  }

  restart(): void {
    if (this.retry) { clearTimeout(this.retry); this.retry = undefined; }
    this.buf = '';
    this.proc?.kill();          // 'close' handler reschedules after all output drains
    if (!this.proc) { this.spawnOnce(); }
  }

  private command(): { cmd: string; args: string[] } {
    const cfg = vscode.workspace.getConfiguration('pengupool').get<string>('command', 'pengupool');
    return { cmd: cfg, args: ['serve'] };
  }

  private spawnOnce(): void {
    if (this.stopped || this.proc) { return; }
    this.buf = '';
    const { cmd, args } = this.command();
    try {
      this.proc = spawn(cmd, args, { env: process.env });
    } catch (err) {
      this.output.appendLine(`[pengupool] spawn failed: ${String(err)}`);
      this._onSpawnError.fire(err as Error);
      this.scheduleRestart();
      return;
    }
    this.proc.stdout.setEncoding('utf8');
    this.proc.stdout.on('data', (chunk: string) => this.ingest(chunk));
    this.proc.stderr.setEncoding('utf8');
    this.proc.stderr.on('data', (d: string) => this.output.append(`[serve] ${d}`));
    this.proc.on('error', (err) => {
      this.output.appendLine(`[pengupool] spawn failed: ${String(err)}`);
      this._onSpawnError.fire(err);
    });
    // 'close' follows both normal exits and spawn errors, so retry exactly once.
    this.proc.on('close', (code) => {
      this.proc = undefined;
      this.output.appendLine(`[pengupool] serve exited (code ${code})`);
      if (!this.stopped) { this.scheduleRestart(); }
    });
  }

  private ingest(chunk: string): void {
    this.buf += chunk;
    let nl: number;
    while ((nl = this.buf.indexOf('\n')) >= 0) {
      const line = this.buf.slice(0, nl).trim();
      this.buf = this.buf.slice(nl + 1);
      if (!line) { continue; }
      try {
        const snap = JSON.parse(line) as Snapshot;
        this.backoff = 500;  // reset only once the backend actually delivers a snapshot
        this._last = snap;
        this._onSnapshot.fire(snap);
      } catch (err) {
        this.output.appendLine(`[pengupool] bad snapshot line: ${String(err)}`);
      }
    }
  }

  private scheduleRestart(): void {
    if (this.stopped || this.retry) { return; }
    const wait = this.backoff;
    this.backoff = Math.min(this.backoff * 2, 10000);
    this.retry = setTimeout(() => {
      this.retry = undefined;
      this.spawnOnce();
    }, wait);
  }

  dispose(): void {
    this.stopped = true;
    if (this.retry) { clearTimeout(this.retry); this.retry = undefined; }
    this.proc?.kill();
    this._onSnapshot.dispose();
    this._onSpawnError.dispose();
  }
}
