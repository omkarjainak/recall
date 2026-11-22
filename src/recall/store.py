"""A thin, safe wrapper around the relate Python client.

relate connections are thread-affine (threadsafety = 1), and MCP hosts run
tool calls on worker threads — so the Store owns one dedicated thread that
opens the connection and executes every statement. Callers anywhere marshal
their work onto that thread. One process, one store, one writing open.
"""

from __future__ import annotations

import datetime as _dt
import queue
import threading
from concurrent.futures import Future
from typing import Any, Callable, TypeVar

import relate

Row = dict[str, Any]
T = TypeVar("T")


def to_iso(value: Any) -> str | None:
    """Normalize a relate/Python temporal value to an ISO-8601 string."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, _dt.datetime):
        v = value
        if v.tzinfo is None:
            v = v.replace(tzinfo=_dt.timezone.utc)
        return v.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    text = getattr(value, "text", None)  # relate.Temporal
    if isinstance(text, str):
        return text
    native = getattr(value, "to_native", None)
    if callable(native):
        return to_iso(native())
    return str(value)


def utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)  # naive input is UTC
    return dt


class Store:
    """A relate store with dict-shaped results, transactions, and thread safety."""

    def __init__(self, path: str = ":memory:", *, read_only: bool = False) -> None:
        self.path = path
        self.read_only = read_only
        self._conn: relate.Connection | None = None
        self._queue: queue.Queue = queue.Queue()
        self._owner = threading.Thread(target=self._loop, name="recall-store", daemon=True)
        self._owner_id: int | None = None
        self._closed = False
        self._owner.start()
        self._submit(lambda: self._open())

    # -- the owner thread --------------------------------------------------
    def _open(self) -> None:
        self._conn = relate.connect(database=self.path, read_only=self.read_only)

    def _loop(self) -> None:
        self._owner_id = threading.get_ident()
        while True:
            item = self._queue.get()
            if item is None:
                return
            fn, fut = item
            try:
                fut.set_result(fn())
            except BaseException as e:  # propagate to the caller
                fut.set_exception(e)

    def _submit(self, fn: Callable[[], T]) -> T:
        if threading.get_ident() == self._owner_id:
            return fn()  # already on the owner thread (e.g. inside transact)
        if self._closed:
            raise RuntimeError("store is closed")
        fut: Future = Future()
        self._queue.put((fn, fut))
        return fut.result()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        if self._closed:
            return
        self._submit(self._close_impl)
        self._closed = True
        self._queue.put(None)  # stop the owner thread
        self._owner.join(timeout=5)

    def _close_impl(self) -> None:
        assert self._conn is not None
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- queries -----------------------------------------------------------
    def run(self, statement: str, params: dict[str, Any] | None = None) -> list[Row]:
        """Run one statement and return its rows as plain dicts."""

        def _do() -> list[Row]:
            assert self._conn is not None
            cur = self._conn.execute(statement, params or None)
            rows: list[Row] = [dict(r) for r in cur.fetchall()]
            cur.consume()
            return rows

        return self._submit(_do)

    def write(self, statement: str, params: dict[str, Any] | None = None) -> list[Row]:
        """Run one statement in its own transaction (auto-commit is on)."""
        return self.run(statement, params)

    def transact(self, fn: Callable[["Store"], T]) -> T:
        """Run several statements atomically on the owner thread."""

        def _do() -> T:
            assert self._conn is not None
            self._conn.autocommit = False
            try:
                result = fn(self)  # nested run() calls execute directly
                self._conn.commit()
                return result
            except BaseException:
                self._conn.rollback()
                raise
            finally:
                self._conn.autocommit = True

        return self._submit(_do)

    def flush(self) -> None:
        def _do() -> None:
            assert self._conn is not None
            self._conn.flush()

        self._submit(_do)
