from __future__ import annotations

import argparse
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from .collector import SnapshotReader, WorkspaceSnapshot, render_metrics
from .config import Config, ConfigurationError, load_config
from .database import DatabaseError, PostgresSnapshotReader, database_dsn_from_environment


class ExporterState:
    def __init__(self, reader: SnapshotReader, config: Config) -> None:
        self.reader = reader
        self.config = config
        self.last_success_timestamp = 0.0
        self.last_duration = 0.0
        self.has_succeeded = False

    def collect(self) -> tuple[list[WorkspaceSnapshot], bool]:
        started = time.time()
        try:
            snapshots = self.reader.collect(self.config.allowed_workspaces)
        except DatabaseError:
            self.last_duration = time.time() - started
            return [], False
        self.last_duration = time.time() - started
        self.last_success_timestamp = time.time()
        self.has_succeeded = True
        return snapshots, True


class MetricsHandler(BaseHTTPRequestHandler):
    state: ClassVar[ExporterState]

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/metrics":
            snapshots, healthy = self.state.collect()
            body = render_metrics(
                snapshots,
                success_timestamp=self.state.last_success_timestamp,
                duration=self.state.last_duration,
                healthy=healthy,
            ).encode()
            self.send_response(HTTPStatus.OK)
            content_type = "text/plain; version=0.0.4; charset=utf-8"
        elif self.path == "/healthz":
            body = b"ok\n" if self.state.has_succeeded else b"no successful database snapshot yet\n"
            self.send_response(
                HTTPStatus.OK if self.state.has_succeeded else HTTPStatus.SERVICE_UNAVAILABLE
            )
            content_type = "text/plain; charset=utf-8"
        else:
            body = b"not found\n"
            self.send_response(HTTPStatus.NOT_FOUND)
            content_type = "text/plain; charset=utf-8"
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only Prometheus exporter for Honcho PostgreSQL"
    )
    parser.add_argument(
        "--config", type=Path, required=True, help="JSON config without credentials"
    )
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        reader = PostgresSnapshotReader(database_dsn_from_environment())
    except (ConfigurationError, DatabaseError) as exc:
        parser.error(str(exc))
    state = ExporterState(reader, config)
    handler = type("ConfiguredMetricsHandler", (MetricsHandler,), {"state": state})
    ThreadingHTTPServer((config.listen, config.port), handler).serve_forever()


if __name__ == "__main__":
    main()
