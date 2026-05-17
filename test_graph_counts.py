"""Unit tests for the per-graph triple-count collector.

Network I/O is stubbed via monkeypatch so these tests are hermetic.
What we actually want to cover:

  * Empty TRACKED_GRAPHS is a no-op (default config)
  * Rate-limit honoured — a recent successful sample is not re-queried
  * Successful sample updates the gauge + the last-refresh timestamp
  * SPARQL failures bump the failure counter and leave the gauge alone
"""
from __future__ import annotations

import os

os.environ.setdefault("DBA_PASSWORD", "test")

import exporter  # noqa: E402  (env must be set first)


def _reset_module_state() -> None:
    exporter._LAST_GRAPH_COUNTS.clear()
    exporter.graph_triple_count._metrics.clear()
    exporter.graph_count_failures._metrics.clear()
    exporter.graph_count_last_refresh._metrics.clear()


def test_collect_graph_counts_noop_when_no_graphs_configured(monkeypatch):
    monkeypatch.setattr(exporter, "TRACKED_GRAPHS", [])
    called = {"n": 0}
    monkeypatch.setattr(
        exporter, "_sparql_count_graph",
        lambda *_a, **_kw: called.__setitem__("n", called["n"] + 1) or 0,
    )
    exporter.collect_graph_counts(now=1000.0)
    assert called["n"] == 0


def test_collect_graph_counts_updates_gauge_on_success(monkeypatch):
    _reset_module_state()
    monkeypatch.setattr(
        exporter, "TRACKED_GRAPHS",
        ["http://data.fontem.eu/graph/sanctions"],
    )
    monkeypatch.setattr(
        exporter, "_sparql_count_graph", lambda *_a, **_kw: 123_456,
    )
    exporter.collect_graph_counts(now=1000.0)
    metric = exporter.graph_triple_count.labels(
        graph="http://data.fontem.eu/graph/sanctions",
    )
    assert metric._value.get() == 123_456


def test_collect_graph_counts_respects_rate_limit(monkeypatch):
    _reset_module_state()
    monkeypatch.setattr(
        exporter, "TRACKED_GRAPHS",
        ["http://data.fontem.eu/graph/sanctions"],
    )
    monkeypatch.setattr(exporter, "GRAPH_COUNT_INTERVAL_SECONDS", 900)
    calls = []
    monkeypatch.setattr(
        exporter, "_sparql_count_graph",
        lambda iri, **_kw: (calls.append(iri), 1)[1],
    )
    exporter.collect_graph_counts(now=1000.0)
    # Same tick + 100s later: rate-limit kicks in, no second call.
    exporter.collect_graph_counts(now=1100.0)
    assert len(calls) == 1
    # > 900s later: re-query allowed.
    exporter.collect_graph_counts(now=2000.0)
    assert len(calls) == 2


def test_collect_graph_counts_failure_increments_counter(monkeypatch):
    _reset_module_state()
    monkeypatch.setattr(
        exporter, "TRACKED_GRAPHS",
        ["http://data.fontem.eu/graph/sanctions"],
    )

    def boom(*_a, **_kw):
        raise RuntimeError("simulated SPARQL timeout")
    monkeypatch.setattr(exporter, "_sparql_count_graph", boom)

    exporter.collect_graph_counts(now=1000.0)
    counter = exporter.graph_count_failures.labels(
        graph="http://data.fontem.eu/graph/sanctions",
    )
    assert counter._value.get() == 1
    # Failure must NOT update _LAST_GRAPH_COUNTS — next tick should retry.
    exporter.collect_graph_counts(now=1001.0)
    assert counter._value.get() == 2


def test_collect_graph_counts_handles_multiple_graphs(monkeypatch):
    _reset_module_state()
    graphs = [
        "http://data.fontem.eu/graph/sanctions",
        "http://data.fontem.eu/graph/eu/eurovoc",
        "http://data.fontem.eu/graph/wikidata/truthy",
    ]
    monkeypatch.setattr(exporter, "TRACKED_GRAPHS", graphs)
    sizes = {g: i * 1_000_000 + 7 for i, g in enumerate(graphs, start=1)}
    monkeypatch.setattr(
        exporter, "_sparql_count_graph",
        lambda iri, **_kw: sizes[iri],
    )
    exporter.collect_graph_counts(now=1000.0)
    for g, expected in sizes.items():
        m = exporter.graph_triple_count.labels(graph=g)
        assert m._value.get() == expected, f"{g}: {m._value.get()} != {expected}"
