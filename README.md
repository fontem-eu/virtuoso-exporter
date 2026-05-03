# virtuoso-exporter

Prometheus exporter for OpenLink Virtuoso 7. Sidecar that
polls the server via `isql status('')` + a SPARQL `ASK {}`
probe and exposes `/metrics` on `:9477`.

See [RUNBOOK.md](RUNBOOK.md) for the metric list, sidecar
wiring, and release flow.
