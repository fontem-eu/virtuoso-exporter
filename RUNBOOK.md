# virtuoso-exporter runbook

Prometheus exporter for OpenLink Virtuoso 7. Sidecar pattern:
runs in the same pod as the server, scrapes via localhost,
exposes `/metrics` on `:9477`.

## Metrics

| Metric | Type | Source |
|---|---|---|
| `virtuoso_up` | gauge | 1 if last `status('')` succeeded |
| `virtuoso_buffers_total/used/dirty` | gauge | `status('')` parse |
| `virtuoso_buffer_reads_total` | counter | synthetic (100/scrape) — pair with `_hit_total` for `rate()` |
| `virtuoso_buffer_reads_hit_total` | counter | synthetic, derived from cache hit ratio |
| `virtuoso_threads_active` / `virtuoso_clients_active` | gauge | `status('')` parse |
| `virtuoso_db_pages_total/used` | gauge | `status('')` parse |
| `virtuoso_last_checkpoint_age_seconds` | gauge | `status('')` parse |
| `virtuoso_sparql_ask_duration_seconds` | histogram | probe `ASK {}` per scrape |
| `virtuoso_sparql_ask_failures_total` | counter | probe failures |

## Why `status('')` text parsing instead of `sys_stat` views
The OS edition of Virtuoso 7 doesn't ship the cluster-mode
`sys_stat` views. `status('')` is the supported diagnostic
surface and prints a stable, parseable text format. The trade-
off is forgiving regexes — if a line is missing, the
corresponding gauge is left unset rather than zero, so PromQL
queries naturally degrade without raising false alerts.

## Configuration

| Env | Default | Purpose |
|---|---|---|
| `DBA_PASSWORD` | (required) | The Virtuoso dba password. Pull from the same secret the server uses. |
| `VIRTUOSO_DBA_USER` | `dba` | DBA user name |
| `VIRTUOSO_ISQL_PORT` | `1111` | isql endpoint |
| `VIRTUOSO_SPARQL_URL` | `http://127.0.0.1:8890/sparql` | SPARQL probe endpoint |
| `EXPORTER_PORT` | `9477` | HTTP listen port |
| `SCRAPE_INTERVAL_SECONDS` | `15` | Internal scrape cadence |

## Sidecar wiring

In the Virtuoso StatefulSet:
```
containers:
  - name: virtuoso          # existing
    ...
  - name: exporter
    image: contribute.void42.internal/golden/virtuoso-exporter:0.1.0
    env:
      - name: DBA_PASSWORD
        valueFrom:
          secretKeyRef: { name: virtuoso-credentials, key: VIRTUOSO_DBA_PASSWORD }
    ports:
      - { name: metrics, containerPort: 9477 }
    resources:
      requests: { cpu: 10m, memory: 32Mi }
      limits:   { cpu: 100m, memory: 64Mi }
```

A ServiceMonitor selecting `port: metrics` finishes the wiring.

## Releasing

```
git tag v0.1.0
git push --tags
```

Workflow builds, pushes, signs (cosign), attaches a CycloneDX
SBOM. Verify externally with `cosign verify`.
