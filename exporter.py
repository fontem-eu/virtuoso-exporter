"""Virtuoso 7 OS edition -> Prometheus exporter.

Polls the live server every scrape via two paths:
  * `isql ... exec="status('');"` for buffer/disk/thread state
  * SPARQL HTTP endpoint for query-path liveness + latency

Designed to run as a sidecar inside the same pod as Virtuoso:
talks to localhost on isql/HTTP ports, reads DBA_PASSWORD from
the same secret the server uses, exposes /metrics on :9477.

Why we parse `status('')` text instead of querying sys_stat:
sys_stat doesn't exist in vanilla Virtuoso 7 OS. status('') is
the supported diagnostic surface and prints a stable, line-
oriented format. Regexes are forgiving — if a line is missing,
the gauge is left unset rather than zero, so PromQL queries
naturally tolerate format drift across patch versions.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

ISQL_PORT = int(os.environ.get("VIRTUOSO_ISQL_PORT", "1111"))
SPARQL_URL = os.environ.get(
    "VIRTUOSO_SPARQL_URL", "http://127.0.0.1:8890/sparql"
)
DBA_USER = os.environ.get("VIRTUOSO_DBA_USER", "dba")
DBA_PASSWORD = os.environ["DBA_PASSWORD"]
LISTEN_PORT = int(os.environ.get("EXPORTER_PORT", "9477"))
SCRAPE_INTERVAL_SECONDS = int(os.environ.get("SCRAPE_INTERVAL_SECONDS", "15"))
ISQL_BIN = os.environ.get("ISQL_BIN", "/opt/virtuoso-opensource/bin/isql")

# Per-graph triple-count metric. The COUNT(*) over a named graph is
# expensive on Virtuoso CE (linear scan; no native cardinality counter)
# so we cap how often we re-run it. Default once every 15 minutes is
# fine for a load-verification metric — the data-quality dashboard
# refreshes on the user's timescale, not Prometheus's.
TRACKED_GRAPHS_RAW = os.environ.get("TRACKED_GRAPHS", "")
TRACKED_GRAPHS = [g.strip() for g in TRACKED_GRAPHS_RAW.split(",") if g.strip()]
GRAPH_COUNT_INTERVAL_SECONDS = int(
    os.environ.get("GRAPH_COUNT_INTERVAL_SECONDS", "900")
)

# ── Metrics ─────────────────────────────────────────────────
up = Gauge("virtuoso_up", "1 if the last scrape succeeded; 0 otherwise")

buffers_total = Gauge("virtuoso_buffers_total", "Total buffer pool slots")
buffers_used = Gauge("virtuoso_buffers_used", "Buffer pool slots in use")
buffers_dirty = Gauge("virtuoso_buffers_dirty", "Dirty buffer slots")

db_pages_total = Gauge("virtuoso_db_pages_total", "Total database pages")
db_pages_free = Gauge("virtuoso_db_pages_free", "Free database pages")
db_file_bytes = Gauge("virtuoso_db_file_bytes", "Database file size in bytes")
pages_changed_since_backup = Gauge(
    "virtuoso_pages_changed_since_backup",
    "Pages mutated since the last backup checkpoint marker",
)

threads_running = Gauge(
    "virtuoso_threads_running", "Threads currently running"
)
threads_waiting = Gauge(
    "virtuoso_threads_waiting", "Threads waiting on locks/IO"
)
clients_concurrent = Gauge(
    "virtuoso_clients_concurrent", "Currently connected clients"
)
clients_max_seen = Gauge(
    "virtuoso_clients_max_seen",
    "Peak concurrent clients since server start",
)

rss_bytes = Gauge("virtuoso_rss_bytes", "Resident set size of virtuoso-t")
vsz_bytes = Gauge("virtuoso_vsz_bytes", "Virtual memory size of virtuoso-t")
cpu_percent = Gauge("virtuoso_cpu_percent", "Server-reported CPU%")

uptime_seconds = Gauge(
    "virtuoso_uptime_seconds", "Server uptime in seconds"
)
deadlocks_total = Counter(
    "virtuoso_deadlocks_total", "Cumulative deadlocks reported"
)
_LAST_DEADLOCKS = {"value": 0}  # for delta tracking

sparql_ask_latency = Histogram(
    "virtuoso_sparql_ask_duration_seconds",
    "Round-trip latency of the probe SPARQL ASK against /sparql",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
sparql_ask_failures = Counter(
    "virtuoso_sparql_ask_failures_total", "Probe SPARQL ASK failures"
)

graph_triple_count = Gauge(
    "virtuoso_graph_triple_count",
    "Triples in a named graph (cached; refreshed every "
    "GRAPH_COUNT_INTERVAL_SECONDS).",
    labelnames=("graph",),
)
graph_count_failures = Counter(
    "virtuoso_graph_count_failures_total",
    "Failed per-graph SPARQL COUNT queries.",
    labelnames=("graph",),
)
graph_count_last_refresh = Gauge(
    "virtuoso_graph_count_last_refresh_timestamp",
    "Unix time of the last successful COUNT(*) for a graph.",
    labelnames=("graph",),
)

# Internal: when each graph was last successfully sampled. Drives the
# rate-limit so an expensive COUNT doesn't run on every 15s scrape tick.
_LAST_GRAPH_COUNTS: dict[str, float] = {}


# ── Status parsing ──────────────────────────────────────────
# Reference output (Virtuoso 07.20.3240 OS):
#   CPU: 0.00% RSS: 228MB VSZ: 2488MB PF: 270
#   Database Status:
#     File size 56623104, 6912 pages, 6090 free.
#     180000 buffers, 514 used, 0 dirty 0 wired down, ...
#   717 pages have been changed since last backup ...
#   Clients: 1 connects, max 1 concurrent
#   Lock Status: 0 deadlocks of which 0 2r1w, 0 waits,
#      Currently 1 threads running 0 threads waiting 0 threads in vdb.
#   Started on: 2026-05-03 08:34 GMT+0 (up 06:26)

_BUFFER_RE = re.compile(
    r"(\d+)\s+buffers,\s*(\d+)\s+used,\s*(\d+)\s+dirty"
)
_PAGES_RE = re.compile(
    r"File size\s+(\d+),\s*(\d+)\s+pages,\s*(\d+)\s+free"
)
_PAGES_CHANGED_RE = re.compile(
    r"(\d+)\s+pages have been changed since last backup"
)
_CLIENTS_RE = re.compile(
    r"(\d+)\s+connects,\s*max\s+(\d+)\s+concurrent"
)
_THREADS_RE = re.compile(
    r"(\d+)\s+threads\s+running\s+(\d+)\s+threads\s+waiting"
)
_DEADLOCK_RE = re.compile(r"(\d+)\s+deadlocks\s+of which")
_RSS_RE = re.compile(r"RSS:\s*(\d+)([KMG])B\s+VSZ:\s*(\d+)([KMG])B")
_CPU_RE = re.compile(r"CPU:\s*([0-9.]+)%")
_UPTIME_RE = re.compile(r"\(up\s+(\d+):(\d+)(?::(\d+))?\)")

_UNIT_BYTES = {"K": 1024, "M": 1024**2, "G": 1024**3}


def run_isql(stmt: str, timeout: float = 5.0) -> str:
    """Run a single isql exec= statement, return stdout."""
    cmd = [
        ISQL_BIN,
        f"127.0.0.1:{ISQL_PORT}",
        DBA_USER,
        DBA_PASSWORD,
        f"exec={stmt}",
    ]
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=True
    ).stdout


def collect_status() -> bool:
    """Poll status('') and update gauges. Return True on success."""
    try:
        out = run_isql("status('');")
    except Exception:
        return False
    parse_status(out)
    return True


def parse_status(out: str) -> None:
    """Pure parser — separated from I/O to make it unit-testable."""
    if m := _BUFFER_RE.search(out):
        total, used, dirty = (int(x) for x in m.groups())
        buffers_total.set(total)
        buffers_used.set(used)
        buffers_dirty.set(dirty)

    if m := _PAGES_RE.search(out):
        size, total, free = (int(x) for x in m.groups())
        db_file_bytes.set(size)
        db_pages_total.set(total)
        db_pages_free.set(free)

    if m := _PAGES_CHANGED_RE.search(out):
        pages_changed_since_backup.set(int(m.group(1)))

    if m := _CLIENTS_RE.search(out):
        clients_concurrent.set(int(m.group(1)))
        clients_max_seen.set(int(m.group(2)))

    if m := _THREADS_RE.search(out):
        threads_running.set(int(m.group(1)))
        threads_waiting.set(int(m.group(2)))

    if m := _DEADLOCK_RE.search(out):
        cur = int(m.group(1))
        delta = max(0, cur - _LAST_DEADLOCKS["value"])
        if delta:
            deadlocks_total.inc(delta)
        _LAST_DEADLOCKS["value"] = cur

    if m := _RSS_RE.search(out):
        rss_v, rss_u, vsz_v, vsz_u = m.groups()
        rss_bytes.set(int(rss_v) * _UNIT_BYTES[rss_u])
        vsz_bytes.set(int(vsz_v) * _UNIT_BYTES[vsz_u])

    if m := _CPU_RE.search(out):
        cpu_percent.set(float(m.group(1)))

    if m := _UPTIME_RE.search(out):
        h, mi, s = m.groups()
        secs = int(h) * 3600 + int(mi) * 60 + (int(s) if s else 0)
        uptime_seconds.set(secs)


def collect_sparql_probe() -> None:
    """Hit /sparql with ASK {} and record latency."""
    q = urllib.parse.quote("ASK { ?s ?p ?o }")
    url = f"{SPARQL_URL}?query={q}"
    start = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            r.read()
        sparql_ask_latency.observe(time.monotonic() - start)
    except Exception:
        sparql_ask_failures.inc()


def _sparql_count_graph(graph_iri: str, timeout: float = 60.0) -> int:
    """Run a SPARQL COUNT(*) against one named graph and return the
    integer result. Raises on transport or parse failure — caller is
    responsible for failure accounting."""
    # Wrapping the count in a SELECT so the response shape stays
    # uniform regardless of Virtuoso's query-rewrite choices.
    query = (
        "SELECT (COUNT(*) AS ?n) WHERE { GRAPH <"
        + graph_iri
        + "> { ?s ?p ?o } }"
    )
    body = urllib.parse.urlencode({
        "query": query,
        "format": "application/sparql-results+json",
        "timeout": str(int(timeout * 1000)),
    }).encode("utf-8")
    req = urllib.request.Request(
        SPARQL_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/sparql-results+json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout + 5) as r:
        payload = json.load(r)
    bindings = payload.get("results", {}).get("bindings", [])
    if not bindings:
        return 0
    return int(bindings[0]["n"]["value"])


def collect_graph_counts(now: float | None = None) -> None:
    """Refresh per-graph triple counts, rate-limited by
    GRAPH_COUNT_INTERVAL_SECONDS. ``now`` is overridable for tests."""
    if not TRACKED_GRAPHS:
        return
    if now is None:
        now = time.monotonic()
    for graph in TRACKED_GRAPHS:
        last = _LAST_GRAPH_COUNTS.get(graph, 0.0)
        if now - last < GRAPH_COUNT_INTERVAL_SECONDS:
            continue
        try:
            n = _sparql_count_graph(graph)
        except Exception:
            graph_count_failures.labels(graph=graph).inc()
            # Don't update _LAST_GRAPH_COUNTS — let the next tick retry.
            continue
        graph_triple_count.labels(graph=graph).set(n)
        graph_count_last_refresh.labels(graph=graph).set(time.time())
        _LAST_GRAPH_COUNTS[graph] = now


def scrape_loop() -> None:
    while True:
        ok_status = collect_status()
        collect_sparql_probe()
        collect_graph_counts()
        up.set(1 if ok_status else 0)
        time.sleep(SCRAPE_INTERVAL_SECONDS)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        body = generate_latest(REGISTRY)
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPE_LATEST)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def install_stop_handlers(server, register=signal.signal) -> None:
    """Stop serving when Kubernetes asks.

    The ENTRYPOINT is exec-form ``python -u exporter.py``, so the
    interpreter is PID 1 — and Linux does not apply default signal
    actions to PID 1: a signal with no handler *registered* is ignored
    outright, not fatal. Without this SIGTERM does nothing and the pod
    only dies when the grace period expires and the kernel sends
    SIGKILL, which stalls every node drain it is caught in.

    shutdown() is dispatched to another thread deliberately: it blocks
    until serve_forever() returns, and the handler runs on the thread
    that is *inside* serve_forever, so calling it directly deadlocks.
    """

    def _stop(signum, _frame):
        print(f"exporter: signal {signum} received, stopping", flush=True)
        Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        register(sig, _stop)


def main() -> None:
    # Daemon, so it cannot hold the process open once serving stops.
    Thread(target=scrape_loop, daemon=True).start()
    server = HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    install_stop_handlers(server)
    try:
        server.serve_forever()
    finally:
        # Releases the listening socket rather than leaving the kernel
        # to reap it at exit.
        server.server_close()


if __name__ == "__main__":
    main()
