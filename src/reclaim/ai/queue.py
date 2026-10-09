from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from reclaim.model import Advice, Item

NORMAL = 1
HIGH = 0
PAUSE_AFTER_FAILURES = 3


@dataclass
class Outcome:
    """What reviewing one item produced. advice may be set even when error is (offline checks)."""

    advice: Advice | None = None
    error: str | None = None


# review_one(item, on_event, fresh) -> Outcome. on_event(kind, text): "step" progress line or "chunk" of streamed reply.
ReviewOne = Callable[[Item, Callable[[str, str], None], bool], Outcome]
# emit(event, item_id, text, outcome): "start", "step", "chunk", "done" (text = error or "", outcome set), "paused".
Emit = Callable[[str, str, str, Outcome | None], None]


@dataclass
class _Entry:
    item: Item
    priority: int
    seq: int
    fresh: bool = False


class ReviewQueue:
    """Reviews items one at a time on a background thread. Highest priority first, then largest, then oldest.

    Thread-safe. Callbacks run on the worker thread."""

    def __init__(self, review_one: ReviewOne, emit: Emit):
        self._review_one = review_one
        self._emit_raw = emit
        self._cond = threading.Condition()
        self._pending: dict[str, _Entry] = {}
        self._state: dict[str, str] = {}
        self._seq = 0
        self._generation = 0
        self._failures = 0
        self._paused = False
        self._stopped = False
        self._thread: threading.Thread | None = None

    # --- producers ---
    def put(self, item: Item, priority: int = NORMAL, fresh: bool = False) -> bool:
        """Queue an item unless it is already queued, running or done. Returns whether it was queued."""
        with self._cond:
            if self._state.get(item.id) in ("queued", "running", "done", "failed") and not fresh:
                return False
            return self._enqueue(item, priority, fresh)

    def bump(self, item: Item) -> str:
        """Review this item next. Returns "running", "queued" (moved up) or "requeued" (reviewed again, skipping the cache)."""
        with self._cond:
            state = self._state.get(item.id)
            if state == "running":
                return "running"
            self._paused = False
            self._failures = 0
            if state == "queued":
                self._pending[item.id].priority = HIGH
                self._cond.notify()
                return "queued"
            self._enqueue(item, HIGH, fresh=state is not None)
            return "requeued"

    def clear(self) -> None:
        """Forget everything. A review already running finishes but its result is dropped."""
        with self._cond:
            self._generation += 1
            self._pending.clear()
            self._state.clear()
            self._failures = 0
            self._paused = False

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._generation += 1
            self._cond.notify_all()

    # --- readers ---
    def state(self, item_id: str) -> str | None:
        with self._cond:
            return self._state.get(item_id)

    def counts(self) -> dict[str, int]:
        with self._cond:
            out = {"queued": 0, "running": 0, "done": 0, "failed": 0}
            for state in self._state.values():
                out[state] += 1
            return out

    @property
    def paused(self) -> bool:
        with self._cond:
            return self._paused

    def idle(self) -> bool:
        with self._cond:
            return not self._pending and "running" not in self._state.values()

    # --- internals ---
    def _emit(self, event: str, item_id: str, text: str, outcome: Outcome | None) -> None:
        """A failing listener (say, a UI that is closing) must never stop the queue."""
        try:
            self._emit_raw(event, item_id, text, outcome)
        except Exception:
            pass

    def _enqueue(self, item: Item, priority: int, fresh: bool) -> bool:
        self._seq += 1
        self._pending[item.id] = _Entry(item, priority, self._seq, fresh)
        self._state[item.id] = "queued"
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="review-queue", daemon=True)
            self._thread.start()
        self._cond.notify()
        return True

    def _next(self) -> tuple[_Entry, int] | None:
        with self._cond:
            while not self._stopped:
                if self._pending and not self._paused:
                    entry = min(self._pending.values(), key=lambda e: (e.priority, -e.item.size_bytes, e.seq))
                    del self._pending[entry.item.id]
                    self._state[entry.item.id] = "running"
                    return entry, self._generation
                self._cond.wait()
        return None

    def _loop(self) -> None:
        while (picked := self._next()) is not None:
            entry, generation = picked
            item = entry.item
            live = lambda: generation == self._generation  # noqa: E731

            def on_event(kind: str, text: str) -> None:
                if live():
                    self._emit(kind, item.id, text, None)

            self._emit("start", item.id, "", None)
            try:
                outcome = self._review_one(item, on_event, entry.fresh)
            except Exception as exc:
                outcome = Outcome(None, str(exc) or exc.__class__.__name__)
            with self._cond:
                if generation != self._generation:
                    continue
                self._state[item.id] = "failed" if outcome.error else "done"
                self._failures = self._failures + 1 if outcome.error else 0
                just_paused = not self._paused and self._failures >= PAUSE_AFTER_FAILURES
                if just_paused:
                    self._paused = True
            self._emit("done", item.id, outcome.error or "", outcome)
            if just_paused:
                self._emit("paused", "", outcome.error or "", None)
