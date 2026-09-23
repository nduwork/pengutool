"""The tree cursor must stay on the chosen session across a transient snapshot drop
(a session blips out for a poll or two on new/resume), not jump to the first row."""
import asyncio

from textual.widgets import Tree

from pengupool.app import ListApp
from pengupool.model import Node


def nodes(*ids):
    return [Node(i, f"sess-{i}", "/tmp", 100 + n, "active", "") for n, i in enumerate(ids)]


async def _scenario():
    app = ListApp()
    async with app.run_test() as pilot:
        tree = app.query_one("#sessions", Tree)

        # user is on session "b"
        app.roots = nodes("a", "b", "c")
        app._want = "b"
        app._rebuild_tree()
        await pilot.pause()
        assert app.selected().session_id == "b"

        # "b" blips out of one snapshot — cursor must NOT jump to the first row ("a")
        app.roots = nodes("a", "c")
        app._rebuild_tree()
        await pilot.pause()
        assert app.selected().session_id != "a"
        assert app._want == "b"  # intention preserved through the gap

        # "b" comes back — cursor snaps back to it
        app.roots = nodes("a", "b", "c")
        app._rebuild_tree()
        await pilot.pause()
        assert app.selected().session_id == "b"

        # a genuine navigation updates the sticky target
        for leaf in tree.root.children:
            if leaf.data.session_id == "c":
                tree.move_cursor(leaf)
        await pilot.pause()
        assert app._want == "c"


def test_selection_survives_transient_drop():
    asyncio.run(_scenario())
