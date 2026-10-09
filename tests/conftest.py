import pytest

from reclaim.context import RunResult, ScanContext


class FakeRun:
    def __init__(self, table=None):
        self.table = {tuple(k): v for k, v in (table or {}).items()}
        self.calls = []

    def __call__(self, argv, cwd=None, timeout=60):
        self.calls.append(tuple(argv))
        return self.table.get(tuple(argv))


def ok(stdout="", code=0):
    return RunResult(code, stdout)


@pytest.fixture
def make_ctx(tmp_path):
    def factory(**kw):
        base = dict(
            home=tmp_path,
            min_size=0,
            which=lambda n: "/bin/" + n,
            sizer=lambda p: 1000,
            is_running=lambda n: False,
            run=FakeRun(),
            now=lambda: 1_800_000_000.0,
            env={},
        )
        base.update(kw)
        return ScanContext(**base)

    return factory
