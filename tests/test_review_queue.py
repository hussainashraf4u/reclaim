import threading
import time

from reclaim.ai.queue import HIGH, Outcome, ReviewQueue
from reclaim.model import Advice, AdviceLevel, Item, Risk


def item(id, size=1):
    return Item(id, id, "G", size, Risk.SAFE)


def wait(cond, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def make(review=None):
    seen, events = [], []
    gate = threading.Event()

    def default(it, on_event, fresh):
        seen.append((it.id, fresh))
        on_event("step", f"step {it.id}")
        return Outcome(Advice(AdviceLevel.OK, "fine"))

    q = ReviewQueue(review or default, lambda *e: events.append(e[:3]))
    return q, seen, events, gate


def test_reviews_largest_first_and_dedupes():
    gate = threading.Event()
    order = []

    def slow(it, on_event, fresh):
        gate.wait(3)
        order.append(it.id)
        return Outcome()

    q = ReviewQueue(slow, lambda *e: None)
    q.put(item("first", 1))
    assert wait(lambda: q.state("first") == "running")
    q.put(item("small", 1))
    q.put(item("big", 99))
    assert not q.put(item("big", 99))
    gate.set()
    assert wait(q.idle) and wait(lambda: len(order) == 3)
    assert order == ["first", "big", "small"]
    assert q.counts()["done"] == 3


def test_bump_moves_queued_item_to_front_and_rereviews_done():
    gate = threading.Event()
    order = []

    def slow(it, on_event, fresh):
        gate.wait(3)
        order.append((it.id, fresh))
        return Outcome()

    q = ReviewQueue(slow, lambda *e: None)
    q.put(item("run", 1))
    assert wait(lambda: q.state("run") == "running")
    q.put(item("big", 99))
    low = item("low", 1)
    q.put(low)
    assert q.bump(low) == "queued"
    assert q.bump(item("run")) == "running"
    gate.set()
    assert wait(lambda: len(order) == 3)
    assert [o[0] for o in order] == ["run", "low", "big"]
    assert q.bump(item("big", 99)) == "requeued"
    assert wait(lambda: len(order) == 4) and order[-1] == ("big", True)


def test_clear_drops_pending_and_stale_result():
    gate = threading.Event()
    events = []

    def slow(it, on_event, fresh):
        gate.wait(3)
        return Outcome(Advice(AdviceLevel.OK, "x"))

    q = ReviewQueue(slow, lambda *e: events.append(e[:2]))
    q.put(item("a"))
    assert wait(lambda: q.state("a") == "running")
    q.put(item("b"))
    q.clear()
    gate.set()
    time.sleep(0.2)
    assert q.counts() == {"queued": 0, "running": 0, "done": 0, "failed": 0}
    assert ("done", "a") not in events and ("start", "b") not in events


def test_pauses_after_three_failures_and_bump_resumes():
    events = []
    q = ReviewQueue(lambda it, on, f: Outcome(None, "down"), lambda *e: events.append(e[:2]))
    for n in range(5):
        q.put(item(f"i{n}", 10 - n))
    assert wait(lambda: q.paused)
    assert q.counts()["failed"] == 3 and q.counts()["queued"] == 2
    assert ("paused", "") in events
    q.bump(item("i3", 7))
    assert wait(lambda: q.counts()["failed"] >= 4)


def test_exception_is_a_failure_not_a_crash():
    def boom(it, on, fresh):
        raise RuntimeError("kaput")

    events = []
    q = ReviewQueue(boom, lambda *e: events.append(e))
    q.put(item("a"))
    assert wait(lambda: q.state("a") == "failed")
    done = [e for e in events if e[0] == "done"][0]
    assert done[2] == "kaput"


def test_events_carry_item_id_and_outcome():
    q, seen, events, _ = make()
    q.put(item("a"))
    assert wait(lambda: q.state("a") == "done")
    kinds = [e[0] for e in events]
    assert kinds == ["start", "step", "done"] and all(e[1] == "a" for e in events)
    q.stop()
