"""`pengupool serve` — the shared backend for non-terminal front-ends (the VS Code extension).

Emits the session graph as newline-delimited JSON on stdout, one snapshot per poll tick, reusing
`model.snapshot()` — the exact data the Textual TUI renders. This is the ONE resource shared between
the terminal app and the extension: both read the same model; only the view differs.

Wire format (one JSON object per line):

    {"rev": 7, "ts": 1732., "topo_hash": "ab12…",
     "roots": [ {id,name,cwd,repo,pid,state,status,label,ctx_pct,started,children:[…]} ],
     "cross": [ [src_name, dst_name, label] ],
     "msgs":  [ [ts, src, dst, label, incoming] ]}

`topo_hash` covers structure only (ids + parent/child + cross edges), NOT status — so a client can
relayout its graph only when the hash changes and otherwise just restyle nodes in place (colour,
ctx%, workflow step), which is what makes a 1 Hz refresh jump-free. Idle ticks are skipped: a
snapshot is written only when it differs from the last one, and `rev` counts the ones actually sent.

Run standalone (no Textual import) with `python -m pengupool.serve`, or via `pengupool serve`.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time

from . import model


def _node(n: model.Node) -> dict:
    return {"id": n.session_id, "name": n.name, "cwd": n.cwd, "repo": n.repo, "pid": n.pid,
            "state": n.state, "status": n.status_line, "label": n.label,
            "ctx_pct": n.ctx_pct, "started": n.started, "tmux_pane": n.tmux_pane, "harness": n.harness,
            "summary": n.summary,
            "children": [_node(c) for c in n.children]}


def _topo(roots: list[model.Node], cross: list[model.Edge]) -> str:
    """Hash of structure only (node ids + child order + cross edges), so status-only ticks don't
    trigger a client relayout."""
    def ids(n: model.Node):
        return [n.session_id, [ids(c) for c in n.children]]
    shape = [[ids(r) for r in roots], sorted((s, d) for s, d, _ in cross)]
    return hashlib.sha1(json.dumps(shape).encode()).hexdigest()[:12]


def build() -> dict:
    """One snapshot dict (no `rev`/`ts`; serve() adds those). Reuses the TUI's model.snapshot()."""
    roots, cross, msgs = model.snapshot()
    return {"topo_hash": _topo(roots, cross),
            "roots": [_node(r) for r in roots],
            "cross": [list(e) for e in cross],
            "msgs": [[m.ts, m.src, m.dst, m.label, m.incoming] for m in msgs[-60:]]}


def _poll_interval() -> float:
    cfg = model._json(model.PENGU / "config.json") or {}
    try:
        return min(60.0, max(0.2, float(cfg.get("poll", 1.0))))
    except (TypeError, ValueError):
        return 1.0


def serve(poll: float | None = None, once: bool = False, out=None) -> None:
    out = out or sys.stdout
    interval = poll if poll is not None else _poll_interval()
    rev, last = 0, None
    while True:
        snap = build()
        blob = json.dumps(snap, sort_keys=True)
        if blob != last:                       # skip idle ticks — client only hears real changes
            rev += 1
            last = blob
            out.write(json.dumps({"rev": rev, "ts": time.time(), **snap}) + "\n")
            out.flush()
        if once:
            return
        time.sleep(interval)


def main() -> None:
    serve(once="--once" in sys.argv[1:])


if __name__ == "__main__":
    main()
