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
ARG VIRTUOSO=gitea-http.dev-tools.svc.cluster.local:3000/fontem/virtuoso-opensource-7@sha256:6e93fc5364b16105cfba9d36c37c0bb8538011bdc376dcf6317e1b2b2781c308
FROM ${VIRTUOSO} AS virtuoso

FROM cgr.void42.internal/chainguard/python:latest-dev@sha256:4a9201167f294fa8d32801010a263a45aa18535359e6fc304e6bffc0b3dd5352 AS build
USER root
ENV PIP_INDEX_URL=https://nexus.void42.internal/repository/pypi-proxy/simple/ \
    PIP_TRUSTED_HOST=nexus.void42.internal
RUN python -m venv /venv \
 && /venv/bin/pip install --no-cache-dir prometheus-client==0.21.0 \
 && /venv/bin/pip uninstall -y pip
# isql and the three libraries are copied from the Virtuoso image, so no
# package database here lists them: declare them for the SBOM, from that
# image's own declaration and dpkg database (sbom-declare.py).
COPY --from=virtuoso /usr/share/void42/sbom/declared.json /tmp/virtuoso/declared.json
COPY --from=virtuoso /var/lib/dpkg/status /tmp/virtuoso/status
COPY --from=virtuoso /usr/lib/os-release /tmp/virtuoso/os-release
COPY sbom-declare.py /tmp/sbom-declare.py
RUN mkdir -p /out/usr/share/void42/sbom \
 && python /tmp/sbom-declare.py /tmp/virtuoso/declared.json /tmp/virtuoso/status /tmp/virtuoso/os-release \
      > /out/usr/share/void42/sbom/declared.json

FROM cgr.void42.internal/chainguard/python:latest@sha256:197cf542e9f4dc373864faecd4fd1a4f642622e654e9196881ca852e8fdf26bd
COPY --from=build /venv /venv
COPY --from=build /out/ /
COPY --from=virtuoso /opt/virtuoso-opensource/bin/isql /opt/virtuoso-opensource/bin/isql
# Every library isql links except glibc, from the image it was built with,
# so isql never depends on what the Python runtime happens to ship: the
# runtime moved to OpenSSL 4 (libssl.so.4) in 2026-10 and dropped the
# libssl.so.3 isql needs. None of these sonames is one the Python process
# uses, so the path does not change it.
COPY --from=virtuoso /usr/lib/x86_64-linux-gnu/libssl.so.3 \
                     /usr/lib/x86_64-linux-gnu/libcrypto.so.3 \
                     /usr/lib/x86_64-linux-gnu/libedit.so.2 \
                     /usr/lib/x86_64-linux-gnu/libtinfo.so.6 \
                     /usr/lib/x86_64-linux-gnu/libbsd.so.0 \
                     /usr/lib/x86_64-linux-gnu/libmd.so.0 \
                     /opt/virtuoso-opensource/lib/
# isql loads OpenSSL's legacy provider at start; that libcrypto looks for it
# at the path Ubuntu compiled in, which the Python runtime never uses.
COPY --from=virtuoso /usr/lib/x86_64-linux-gnu/ossl-modules/legacy.so /usr/lib/x86_64-linux-gnu/ossl-modules/legacy.so
ENV PATH="/venv/bin:$PATH" \
    LD_LIBRARY_PATH=/opt/virtuoso-opensource/lib \
    PYTHONUNBUFFERED=1
# isql must load here and now: a missing library fails the build instead of
# leaving virtuoso_up at 0 in production (exec form, the runtime has no shell).
RUN ["/opt/virtuoso-opensource/bin/isql", "-?"]
COPY exporter.py /app/exporter.py
WORKDIR /app
USER 65532
EXPOSE 9477
ENTRYPOINT ["/venv/bin/python", "-u", "exporter.py"]
