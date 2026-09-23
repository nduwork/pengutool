import pytest

from pengupool import harness, model


@pytest.fixture(autouse=True)
def _no_real_pi(tmp_path_factory, monkeypatch):
    """Tests patch CLAUDE/PENGU one by one; keep the pi roots off the real ~/.pi and ~/.pengupool too."""
    root = tmp_path_factory.mktemp("pi")
    monkeypatch.setattr(model, "PI_LIVE", root / "live")
    monkeypatch.setattr(harness, "PI", root / "agent")
