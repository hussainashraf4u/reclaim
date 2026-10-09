from pathlib import Path

from reclaim.model import Item, RemovePath, Risk, Run


def make(**kw):
    base = dict(id="a", label="A", group="Caches", size_bytes=10, risk=Risk.SAFE)
    base.update(kw)
    return Item(**base)


def test_executable_needs_actions():
    assert not make().executable
    assert make(actions=(RemovePath(Path("/x")),)).executable


def test_report_only_and_disabled_are_not_executable():
    acts = (Run(("echo", "hi")),)
    assert not make(actions=acts, report_only=True).executable
    assert not make(actions=acts, disabled_reason="busy").executable


def test_to_json_is_plain_data():
    item = make(actions=(RemovePath(Path("/a b/ü")), Run(("brew", "cleanup"))))
    data = item.to_json()
    assert data["risk"] == "safe"
    assert data["actions"] == [
        {"type": "remove", "path": "/a b/ü"},
        {"type": "run", "argv": ["brew", "cleanup"]},
    ]
    assert data["executable"] is True
