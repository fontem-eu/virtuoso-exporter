# Virtuoso 7 → Prometheus exporter. Runs as a sidecar in the
# same pod as the Virtuoso server so it can hit localhost:1111
# (isql) and localhost:8890 (SPARQL HTTP).
#
# We need the openlink/virtuoso isql client, which only ships
# with the full Virtuoso image. To keep the layer slim we copy
# isql + its shared libs out of the upstream image rather than
# install a second Python+Virtuoso stack.
FROM contribute.void42.internal/golden/virtuoso-opensource-7:7.2.14 AS virtuoso

FROM python:3.13-alpine

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
