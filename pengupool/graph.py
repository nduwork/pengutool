"""Tidy-tree card renderer: Node tree -> list of equal-width text rows (no color)."""
from __future__ import annotations

import re

from rich.cells import cell_len, set_cell_size

from .model import Edge, Node

GLYPH = {"active": "●", "waiting": "◷", "stale": "○", "blocked": "?"}
GAP = 3
MIN_W = 20
CARD_MAX_W = 38          # 3 capped siblings + 2*GAP == 120, so more trees stay in wide form
WRAP_W = CARD_MAX_W - 4  # inner text width of a body row ("│ " … " │")
NAME0_W = CARD_MAX_W - 7  # name budget on the title row ("─ ● " + name + " ")
CARD_H_MAX = 7           # title + (≤1 name overflow + repo + ≤3 chain) + bottom
REPO_GLYPH = "⎇"          # marks the git repo row
NAME_LINES = 2
CHAIN_LINES = 3
LABEL_WORDS = 5   # words per label line
LABEL_LINES = 3   # max lines per label


def _fit(s: str, w: int) -> str:
    """Truncate to w terminal cells (CJK/emoji are 2 cells) and pad so borders stay aligned."""
    s = re.sub(r"\s+", " ", s)  # newlines/tabs would break the canvas row
    return s if cell_len(s) <= w else set_cell_size(s, max(0, w - 1)).rstrip() + "…"


def _cellwrap(s: str, w: int) -> list[str]:
    """Split s into successive ≤w-cell chunks, never splitting a 2-cell char."""
    out, cur = [], ""
    for ch in s:
        if cur and cell_len(cur + ch) > w:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out or [""]


def _split_at(s: str, w: int) -> tuple[str, str]:
    """First ≤w cells of s, preferring a break at a nearby -/_/space; plus the remainder."""
    piece = ""
    for ch in s:
        if cell_len(piece + ch) > w:
            break
        piece += ch
    cut = max((piece.rfind(c) for c in "-_ "), default=-1)
    if cut > 0 and cut >= len(piece) - 8:
        piece = piece[:cut + 1]
    return piece, s[len(piece):]


def wrap_name(name: str) -> list[str]:
    """Session name as ≤NAME_LINES lines: line 0 on the title border, overflow inside the card."""
    name = " ".join(name.split())
    if cell_len(name) <= NAME0_W:
        return [name]
    first, rest = _split_at(name, NAME0_W)
    lines = [first.rstrip()] + _cellwrap(rest, WRAP_W - 3)
    if len(lines) > NAME_LINES:
        lines = lines[:NAME_LINES]
        lines[-1] = _fit(lines[-1] + "…", WRAP_W - 3)
    return lines


def wrap_chain(status_line: str, w: int = WRAP_W) -> list[str]:
    """Workflow chain wrapped on ` → ` boundaries; continuations hang under a leading `→`."""
    s = " ".join((status_line or "").split())
    if not s:
        return ["no chain"]
    segs = s.split(" → ")
    lines, cur = [], segs[0]
    for seg in segs[1:]:
        cand = f"{cur} → {seg}"
        if cell_len(cand) <= w:
            cur = cand
        else:
            lines.append(cur)
            cur = f"  → {seg}"
    lines.append(cur)
    wrapped: list[str] = []
    for ln in lines:  # a single segment wider than the card: cell-wrap it, indent continuations
        if cell_len(ln) <= w:
            wrapped.append(ln)
        else:
            parts = _cellwrap(ln, w)
            wrapped.append(parts[0])
            wrapped += ["    " + p for p in parts[1:]]
    if len(wrapped) > CHAIN_LINES:
        wrapped = wrapped[:CHAIN_LINES]
        wrapped[-1] = _fit(wrapped[-1] + " → …", w)
    return wrapped


def body_lines(n: Node) -> tuple[str, list[str]]:
    """(name for the title row, body rows) — name overflow, `⎇ repo`, then the wrapped chain."""
    nm = wrap_name(n.name)
    body = ["   " + x for x in nm[1:]] + [f"{REPO_GLYPH} {n.repo}"] + wrap_chain(n.status_line or "")
    return nm[0], body


def card_h(n: Node) -> int:
    return 2 + len(body_lines(n)[1])


def wrap_label(label: str) -> list[str]:
    """Message title as a short stack of ~LABEL_WORDS-word lines (at most LABEL_LINES)."""
    words = " ".join(label.split()).split(" ")
    lines = [" ".join(words[i:i + LABEL_WORDS]) for i in range(0, len(words), LABEL_WORDS) if words[i:i + LABEL_WORDS]]
    if len(lines) > LABEL_LINES:
        lines = lines[:LABEL_LINES]
        lines[-1] += "…"
    return [ln for ln in lines if ln]


def card_width(n: Node, max_w: int) -> int:
    title_name, body = body_lines(n)
    title = f"─ {GLYPH[n.state]} {title_name} "
    need = max([cell_len(title) + 2] + [cell_len(b) + 4 for b in body])
    return max(MIN_W, min(min(max_w, CARD_MAX_W), need))


def _width(n: Node, max_w: int) -> int:
    w = max(card_width(n, max_w), max((cell_len(ln) for ln in wrap_label(n.label)), default=0) + 2)
    if n.children:
        w = max(w, sum(_width(c, max_w) for c in n.children) + GAP * (len(n.children) - 1))
    return w


def _pad(s: str, w: int, fill: str = " ") -> str:
    return s + fill * max(0, w - cell_len(s))


class _Canvas:
    def __init__(self, w: int, h: int):
        self.rows = [[" "] * w for _ in range(h)]

    def put(self, r: int, c: int, s: str):
        if not 0 <= r < len(self.rows):
            return
        row = self.rows[r]
        for i, ch in enumerate(s):
            if 0 <= c + i < len(row):
                row[c + i] = ch

    def soft(self, r: int, c: int, ch: str):
        """Write only over blank cells: dotted routes pass behind cards instead of tearing them."""
        if 0 <= r < len(self.rows) and 0 <= c < len(self.rows[r]) and self.rows[r][c] == " ":
            self.rows[r][c] = ch

    def text(self) -> list[str]:
        return ["".join(r).rstrip() for r in self.rows]


def _depth(n: Node) -> int:
    return 1 + max((_depth(c) for c in n.children), default=0)


MAX_CROSS = 8
CROSS_LABEL_W = 32


def render_tree(root: Node, max_w: int = 120, cross: list[Edge] | tuple = ()) -> list[str]:
    """Wide form: rounded cards, labeled branches; non-tree messages (`cross`: ancestor→grandchild,
    replies, siblings) are dotted arrows routed through a gutter on the right. Compact form if too wide."""
    total = _width(root, max_w)
    if total > max_w:
        return render_compact(root)
    names = {n.name for n in _walk(root)}
    cross = [e for e in cross if e[0] in names and e[1] in names and e[0] != e[1]][-MAX_CROSS:]
    gutter = (2 + 2 * len(cross) + CROSS_LABEL_W + 2) if cross else 0
    canvas = _Canvas(total + gutter, _depth(root) * (CARD_H_MAX + 2 + LABEL_LINES))
    pos: dict[str, tuple[int, int, int, int]] = {}
    _draw(canvas, root, 0, 0, max_w, pos)
    _draw_cross(canvas, cross, pos, total)
    rows = canvas.text()
    while rows and not rows[-1]:
        rows.pop()
    return rows


def _walk(n: Node):
    yield n
    for c in n.children:
        yield from _walk(c)


def _draw_cross(cv: _Canvas, cross: list[Edge], pos: dict[str, tuple[int, int, int, int]], total: int) -> None:
    """Each cross edge gets its own gutter column: src ┈┈╮ … ┊ … ◀┈┈╯ dst. Dotted routes only cross
    blank cells and never enter another card; the label sits on the destination row past the gutter."""
    rects = list(pos.values())

    def blocked(r: int, c: int) -> bool:
        return any(rr <= r < rr + hh and ll <= c < ll + ww for rr, ll, ww, hh in rects)

    def dotted(r: int, c: int, ch: str):
        if not blocked(r, c):
            cv.soft(r, c, ch)

    used_rows: set[int] = set()
    label_x = total + 2 + 2 * len(cross) + 1
    for i, (src, dst, label) in enumerate(cross):
        (r1, l1, w1, _h1), (r2, l2, w2, _h2) = pos[src], pos[dst]
        x = total + 1 + 2 * i
        for c in range(l1 + w1, x):
            dotted(r1, c, "┈")
        for c in range(l2 + w2 + 1, x):
            dotted(r2, c, "┈")
        for r in range(min(r1, r2), max(r1, r2) + 1):
            dotted(r, x, "┊")
        if r1 != r2:
            cv.put(r1, x, "╮" if r1 < r2 else "╯")
            cv.put(r2, x, "╯" if r1 < r2 else "╮")
        cv.put(r2, l2 + w2, "◀")  # arrowhead always wins over a passing line
        if label:
            lr = r2
            while lr in used_rows:
                lr += 1
            used_rows.add(lr)
            cv.put(lr, label_x, _fit(label, CROSS_LABEL_W))


def _draw(cv: _Canvas, n: Node, r: int, c: int, max_w: int, pos: dict | None = None, h: int | None = None) -> int:
    """Draw subtree with its left edge at column c, top at row r; `h` = card height (padded to the
    tallest sibling). Returns the card's center col."""
    sub_w = _width(n, max_w)
    w = card_width(n, max_w)
    left = c + (sub_w - w) // 2
    center = left + w // 2
    title_name, body = body_lines(n)
    h = h or (2 + len(body))
    if pos is not None:
        pos[n.name] = (r, left, w, h)
    title = f"─ {GLYPH[n.state]} {title_name} "
    cv.put(r, left, "╭" + _pad(_fit(title, w - 2), w - 2, "─") + "╮")
    for i in range(h - 2):  # body rows, padded with blanks to the sibling height
        line = body[i] if i < len(body) else ""
        cv.put(r + 1 + i, left, "│ " + _pad(_fit(line, w - 4), w - 4) + " │")
    bottom = "╰" + "─" * (w - 2) + "╯"
    if n.children:
        bottom = bottom[: center - left] + "┬" + bottom[center - left + 1:]
    cv.put(r + h - 1, left, bottom)
    if not n.children:
        return center
    many = len(n.children) > 1
    kids_h = max(card_h(ch) for ch in n.children)  # pad siblings so their bottoms/buses align
    wrapped = {id(ch): wrap_label(ch.label) for ch in n.children}
    label_rows = max((len(v) for v in wrapped.values()), default=1) or 1
    bus_r, label_r = r + h, r + h + many
    arrow_r = label_r + label_rows
    child_r = arrow_r + 1
    kids_w = sum(_width(ch, max_w) for ch in n.children) + GAP * (len(n.children) - 1)
    cc = c + (sub_w - kids_w) // 2
    centers = []
    for ch in n.children:
        cw = _width(ch, max_w)
        centers.append(_draw(cv, ch, child_r, cc, max_w, pos, kids_h))
        cc += cw + GAP
    if many:
        cv.put(bus_r, centers[0], "─" * (centers[-1] - centers[0] + 1))
        for x in centers:
            cv.put(bus_r, x, "┬")
        cv.put(bus_r, centers[0], "┌")
        cv.put(bus_r, centers[-1], "┐")
        cv.put(bus_r, center, "┼" if center in centers else "┴")
    for ch, x in zip(n.children, centers):
        lines = wrapped[id(ch)]
        if lines:
            for i, lab in enumerate(lines):
                cv.put(label_r + i, x - cell_len(lab) // 2, lab)
            for i in range(len(lines), label_rows):
                cv.put(label_r + i, x, "│")
        else:
            for i in range(label_rows):
                cv.put(label_r + i, x, "│")
        cv.put(arrow_r, x, "▼")
    return center


def render_compact(root: Node) -> list[str]:
    out = [f"{GLYPH[root.state]} {root.name} {REPO_GLYPH} {root.repo}  {root.status_line or 'no chain'}"]

    def walk(n: Node, prefix: str):
        for i, ch in enumerate(n.children):
            last = i == len(n.children) - 1
            lab = f"({ch.label})" if ch.label else ""
            out.append(f"{prefix}{'└' if last else '├'}─{lab}──▶ {GLYPH[ch.state]} {ch.name} "
                       f"{REPO_GLYPH} {ch.repo}  {ch.status_line or 'no chain'}")
            walk(ch, prefix + ("   " if last else "│  ") + " " * (len(lab) + 4))

    walk(root, "")
    return out
