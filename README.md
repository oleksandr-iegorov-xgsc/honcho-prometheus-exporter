# Honcho Prometheus Exporter

A standalone, **read-only** PostgreSQL exporter for operational Honcho workspace
snapshots. It uses `psycopg[binary]` and Python's standard-library HTTP server.
It never selects message/document content, peer names, session names, metadata,
queue payloads, or database identifiers.

## Security model

- The DSN is accepted **only** from `HONCHO_EXPORTER_DATABASE_DSN` at runtime.
  It is never accepted in JSON configuration, metrics, or logs.
- The PostgreSQL login must be a dedicated role with only `CONNECT`, schema
  `USAGE`, and `SELECT` on Honcho tables. The collector also sets its transaction
  read-only.
- Metrics use workspace labels (either a bounded configured allowlist or the
  bounded set currently present in `workspaces`) plus fixed enum labels only.

Example role provisioning, performed by a database administrator:

```sql
CREATE ROLE honcho_exporter LOGIN PASSWORD 'store-outside-git';
GRANT CONNECT ON DATABASE honcho TO honcho_exporter;
GRANT USAGE ON SCHEMA public TO honcho_exporter;
GRANT SELECT ON TABLE workspaces, peers, sessions, messages, message_embeddings,
  collections, documents, queue, active_queue_sessions TO honcho_exporter;
ALTER ROLE honcho_exporter SET default_transaction_read_only = on;
```

Adjust database/schema names to the deployment. Put the DSN in a root-readable
Podman `--env-file` outside this repository, e.g.
`HONCHO_EXPORTER_DATABASE_DSN=postgresql://honcho_exporter:...@127.0.0.1/honcho`.

## Configuration and run

Copy `examples/config.json` to a protected deployment location. It intentionally
contains no DSN. `allowed_workspaces` may be omitted to discover workspace names;
for strict cardinality control, configure a finite allowlist.

```bash
export HONCHO_EXPORTER_DATABASE_DSN='postgresql://…' # do not put this in shell history in production
uv run honcho-prometheus-exporter --config /etc/honcho-exporter/config.json
```

Endpoints:

- `GET /metrics`: executes a current aggregate-only snapshot and returns gauges.
- `GET /healthz`: performs the initial aggregate-only snapshot and returns 200
  after a successful one; it returns 503 while the database is unavailable. A
  later failed scrape does not erase the last confirmed successful readiness.

Metrics cover peers; active/inactive sessions; messages and tokens; collections;
documents by fixed `level` and `deleted` labels; derived-times sum/max;
message/document embeddings by fixed sync state; queue counts by fixed task type
and state (in-progress derives exclusively from `active_queue_sessions`);
oldest pending age; active queue sessions; and exporter health/timing.

## Container

```bash
podman build -t honcho-prometheus-exporter .
podman run --rm --network host --env-file /secure/honcho-exporter.env \
  -v /etc/honcho-exporter/config.json:/etc/honcho-exporter/config.json:ro \
  honcho-prometheus-exporter --config /etc/honcho-exporter/config.json
```

The image runs as UID 10001 and has no configuration or credentials baked in.

## Development

```bash
uv sync --group dev
uv run pytest -q
uv run ruff check .
uv build
```
