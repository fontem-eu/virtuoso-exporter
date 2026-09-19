# Virtuoso 7 → Prometheus exporter. Runs as a sidecar in the
# same pod as the Virtuoso server so it can hit localhost:1111
# (isql) and localhost:8890 (SPARQL HTTP).
#
# We need the openlink/virtuoso isql client, which only ships
# with the full Virtuoso image. To keep the layer slim we copy
# isql + its shared libs out of the upstream image rather than
# install a second Python+Virtuoso stack.
# Base images are pinned by digest: a tag can be re-pushed upstream and
# change the build with no commit of ours (Docker Hub swapped Virtuoso
# 7.2.17 for a 7.2.18-dev build in August 2026).
FROM contribute.void42.internal/fontem/virtuoso-opensource-7:7.2.16@sha256:e7a5cd1915569d70d8363503dc62f6bf818b485f1501b230c7608cde8528c72d AS virtuoso

FROM python:3.14-alpine@sha256:016508ba505da24f7139765bc4bb669df4e88eb2f12eeadd571bf2f88d7533df

# Pull in libstdc++ (Virtuoso links to it) and bash for the
# isql wrapper.
RUN apk add --no-cache libstdc++ libgcc

COPY --from=virtuoso /opt/virtuoso-opensource/bin/isql /opt/virtuoso-opensource/bin/isql
COPY --from=virtuoso /opt/virtuoso-opensource/lib/ /opt/virtuoso-opensource/lib/

ENV LD_LIBRARY_PATH=/opt/virtuoso-opensource/lib

RUN pip install --no-cache-dir prometheus-client==0.21.0

COPY exporter.py /app/exporter.py
WORKDIR /app

EXPOSE 9477
ENTRYPOINT ["python", "-u", "exporter.py"]
