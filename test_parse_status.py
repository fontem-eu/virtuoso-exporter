"""Unit tests for status('') parsing.

Live regex coverage against a captured status('') sample, so a
Virtuoso patch release that drifts the format breaks these
tests instead of silently zeroing the dashboard.
"""
from __future__ import annotations

import os

# Provide the env the module reads at import time before importing.
os.environ.setdefault("DBA_PASSWORD", "test")

import exporter  # noqa: E402  (env must be set first)


SAMPLE = """\
OpenLink Virtuoso  Server
Version 07.20.3240-pthreads for Linux as of Nov 11 2024 (ffed4676d)
Started on: 2026-05-03 08:34 GMT+0 (up 06:26)
CPU: 0.00% RSS: 228MB VSZ: 2488MB PF: 270

Database Status:
  File size 56623104, 6912 pages, 6090 free.
  180000 buffers, 514 used, 0 dirty 0 wired down, repl age 0 0 w. io 0 w/crsr.
  Disk Usage: 548 reads avg 0 msec, 0% r 0% w last  0 s, 73 writes flush          0 MB/s,
    17 read ahead, batch = 12.  Autocompact 0 in 0 out, 0% saved.
Gate:  98 2nd in reads, 0 gate write waits, 0 in while read 0 busy scrap.
Log = /database/virtuoso.trx, 185 bytes
717 pages have been changed since last backup (in checkpoint state)
Current backup timestamp: 0x0000-0x00-0x00
Last backup date: unknown
Clients: 1 connects, max 1 concurrent
RPC: 4 calls, 1 pending, 1 max until now, 0 queued, 0 burst reads (0%), 0 second 0M large, 44M max
Checkpoint Remap 79 pages, 0 mapped back. 0 s atomic time.
    DB master 6912 total 6090 free 79 remap 0 mapped back
   temp  256 total 251 free

Lock Status: 0 deadlocks of which 0 2r1w, 0 waits,
   Currently 1 threads running 0 threads waiting 0 threads in vdb.
"""


def test_buffers():
    exporter.parse_status(SAMPLE)
    assert exporter.buffers_total._value.get() == 180000
    assert exporter.buffers_used._value.get() == 514
    assert exporter.buffers_dirty._value.get() == 0


def test_pages_and_file_size():
    exporter.parse_status(SAMPLE)
    assert exporter.db_file_bytes._value.get() == 56623104
    assert exporter.db_pages_total._value.get() == 6912
    assert exporter.db_pages_free._value.get() == 6090


def test_pages_changed():
    exporter.parse_status(SAMPLE)
    assert exporter.pages_changed_since_backup._value.get() == 717


def test_clients():
    exporter.parse_status(SAMPLE)
    assert exporter.clients_concurrent._value.get() == 1
    assert exporter.clients_max_seen._value.get() == 1


def test_threads():
    exporter.parse_status(SAMPLE)
    assert exporter.threads_running._value.get() == 1
    assert exporter.threads_waiting._value.get() == 0


def test_memory_and_cpu():
    exporter.parse_status(SAMPLE)
    assert exporter.rss_bytes._value.get() == 228 * 1024 * 1024
    assert exporter.vsz_bytes._value.get() == 2488 * 1024 * 1024
    assert exporter.cpu_percent._value.get() == 0.0


def test_uptime_hh_mm():
    exporter.parse_status(SAMPLE)
    assert exporter.uptime_seconds._value.get() == 6 * 3600 + 26 * 60
