"""`pengupool serve` backend: build() emits the model snapshot as JSON, and topo_hash covers
structure only — a status/ctx change must NOT change it (so the extension restyles in place instead
of relaying out), while a structural change MUST."""
import io
import json

from pengupool import model, serve


def _roots(state="active", ctx=10, child_name="child"):
    child = model.Node("c1", child_name, "/repo", 2, state, status_line="[b] x ●", ctx_pct=ctx,
                       tmux_pane="%3")
    root = model.Node("r1", "root", "/repo", 1, "active", status_line="[b] y ●", ctx_pct=5,
                      children=[child])
    return [root], [("root", "c1", "spun up")], []


def test_build_shape_and_topo_hash(monkeypatch):
    monkeypatch.setattr(model, "snapshot", lambda: _roots())
    snap = serve.build()
    assert snap["roots"][0]["id"] == "r1"
    assert snap["roots"][0]["children"][0]["ctx_pct"] == 10
    assert snap["roots"][0]["children"][0]["tmux_pane"] == "%3"   # extension uses this for "attach vs start"
    assert snap["cross"] == [["root", "c1", "spun up"]]
    base = snap["topo_hash"]

    # status + ctx change only -> same topology hash (client should NOT relayout)
    monkeypatch.setattr(model, "snapshot", lambda: _roots(state="stale", ctx=99))
    assert serve.build()["topo_hash"] == base

    # a structural change (renamed node = new child identity in the shape) -> different hash
    monkeypatch.setattr(model, "snapshot", lambda: ([model.Node("r1", "root", "/repo", 1, "active")], [], []))
    assert serve.build()["topo_hash"] != base


def test_serve_once_writes_one_json_line(monkeypatch):
    monkeypatch.setattr(model, "snapshot", lambda: _roots())
    buf = io.StringIO()
    serve.serve(once=True, out=buf)
    lines = buf.getvalue().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["rev"] == 1 and "topo_hash" in obj and obj["roots"][0]["name"] == "root"
