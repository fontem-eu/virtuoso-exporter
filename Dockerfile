# Virtuoso 7 → Prometheus exporter. Runs as a sidecar in the
# same pod as the Virtuoso server so it can hit localhost:1111
# (isql) and localhost:8890 (SPARQL HTTP).
#
# isql only ships with the Virtuoso image, and it is a glibc binary: the
# previous python:3.14-alpine (musl) runtime had no loader for it, so every
# status('') call failed and virtuoso_up stayed 0. Chainguard's Python is
# glibc and already carries the OpenSSL, lzma, bz2 and tinfo libraries isql
# links against; the three it lacks (libedit, libbsd, libmd) come from our
# Virtuoso image, in a directory of their own.
#
# Bases are pinned by digest (Renovate keeps the Chainguard ones current).
# The Virtuoso image is named by the registry's in-cluster host, the one
# CI's docker-build-sign logs in to; outside the cluster, pass
# --build-arg VIRTUOSO=contribute.void42.internal/... with the same digest.
ARG VIRTUOSO=gitea-http.dev-tools.svc.cluster.local:3000/fontem/virtuoso-opensource-7@sha256:1dec54db8525fe250d58b4c29a8f57c3ba7ef5d8646e274737c150ea88ebf8e7
FROM ${VIRTUOSO} AS virtuoso

FROM cgr.void42.internal/chainguard/python:latest-dev@sha256:82943d7c508865fa97d15d129d524991100e66a8f89450233704d4ea70accd95 AS build
USER root
ENV PIP_INDEX_URL=https://nexus.void42.internal/repository/pypi-proxy/simple/ \
    PIP_TRUSTED_HOST=nexus.void42.internal
RUN python -m venv /venv \
 && /venv/bin/pip install --no-cache-dir prometheus-client==0.21.0 \
 && /venv/bin/pip uninstall -y pip

FROM cgr.void42.internal/chainguard/python:latest@sha256:1961420e5f93bd056d4b0b40eca12cdf01b3ed09177aa4d6ec71fab38cbf158f
COPY --from=build /venv /venv
COPY --from=virtuoso /opt/virtuoso-opensource/bin/isql /opt/virtuoso-opensource/bin/isql
COPY --from=virtuoso /usr/lib/x86_64-linux-gnu/libedit.so.2 \
                     /usr/lib/x86_64-linux-gnu/libbsd.so.0 \
                     /usr/lib/x86_64-linux-gnu/libmd.so.0 \
                     /opt/virtuoso-opensource/lib/
# Only sonames nothing else in the image provides, so the Python process
# is unaffected by this path.
ENV PATH="/venv/bin:$PATH" \
    LD_LIBRARY_PATH=/opt/virtuoso-opensource/lib \
    PYTHONUNBUFFERED=1
COPY exporter.py /app/exporter.py
WORKDIR /app
USER 65532
EXPOSE 9477
ENTRYPOINT ["/venv/bin/python", "-u", "exporter.py"]
