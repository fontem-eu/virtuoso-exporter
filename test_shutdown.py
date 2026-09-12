"""The exporter stops when Kubernetes asks it to.

Pins the behaviour missing on 2026-09-12, when a sibling service ignored
SIGTERM for the full 30-minute drain grace: the ENTRYPOINT is exec-form,
so Python is PID 1, and PID 1 gets no default action for a signal it has
not registered a handler for.
"""
from __future__ import annotations

import os
import signal
import threading

os.environ.setdefault("DBA_PASSWORD", "test")

import exporter  # noqa: E402  pylint: disable=wrong-import-position


class _FakeServer:
    """Stands in for HTTPServer; records that shutdown() was asked for."""

    def __init__(self):
        self.stopped = threading.Event()

    def shutdown(self):
        self.stopped.set()


def _install(server):
    registered = {}
    exporter.install_stop_handlers(server, register=registered.setdefault)
    return registered


def test_registers_handlers_for_both_stop_signals():
    # The registration is the fix — an unregistered signal never reaches
    # a PID 1 process at all.
    registered = _install(_FakeServer())

    assert set(registered) == {signal.SIGTERM, signal.SIGINT}


def test_sigterm_stops_the_server():
    server = _FakeServer()
    registered = _install(server)

    registered[signal.SIGTERM](signal.SIGTERM, None)

    assert server.stopped.wait(timeout=5), "shutdown() was never called"


def test_shutdown_runs_off_the_serving_thread():
    # shutdown() blocks until serve_forever() returns, and the handler
    # runs on the thread sitting inside serve_forever. Calling it inline
    # would deadlock the very shutdown it is trying to perform, so it
    # must happen on another thread.
    server = _FakeServer()
    registered = _install(server)
    calling_thread = threading.current_thread()
    seen = {}

    def record():
        seen["thread"] = threading.current_thread()
        server.stopped.set()

    server.shutdown = record
    registered[signal.SIGTERM](signal.SIGTERM, None)

    assert server.stopped.wait(timeout=5)
    assert seen["thread"] is not calling_thread
