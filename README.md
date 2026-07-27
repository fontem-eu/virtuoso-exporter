> ### 🪞 This GitHub repository is a mirror
>
> Development happens on Fontem's own infrastructure; this mirror is
> updated automatically. **Issues and pull requests opened here are not
> monitored.**
>
> If you would like to contribute — code, data sources, review, or
> anything else — please get in touch at **team@fontem.eu** and we will
> set you up.

# virtuoso-exporter

Prometheus exporter for OpenLink Virtuoso 7. Sidecar that
polls the server via `isql status('')` + a SPARQL `ASK {}`
probe and exposes `/metrics` on `:9477`.

See [RUNBOOK.md](RUNBOOK.md) for the metric list, sidecar
wiring, and release flow.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
