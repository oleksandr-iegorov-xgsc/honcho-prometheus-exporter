from __future__ import annotations

import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from honcho_prometheus_exporter.collector import WorkspaceSnapshot, render_metrics
from honcho_prometheus_exporter.config import ConfigurationError, load_config
from honcho_prometheus_exporter.database import DatabaseError
from honcho_prometheus_exporter.server import ExporterState, MetricsHandler


def test_render_metrics_includes_requested_bounded_workspace_metrics() -> None:
    snapshot = WorkspaceSnapshot(
        workspace="alpha",
        peers=2,
        sessions_active=3,
        sessions_inactive=1,
        messages=7,
        message_tokens=42,
        collections=4,
        documents={"explicit|false": 5, "deductive|true": 1},
        derived_times_sum=11,
        derived_times_max=4,
        message_embeddings={"synced": 6, "pending": 1, "failed": 0},
        document_embeddings={"synced": 5, "pending": 1, "failed": 0},
        queue_items={"summary|pending": 2, "summary|in_progress": 1, "summary|completed": 3},
        oldest_pending_age={"summary": 12.5},
        active_queue_sessions=1,
    )

    text = render_metrics([snapshot], success_timestamp=100.0, duration=0.25, healthy=True)

    assert 'honcho_workspace_peers{workspace="alpha"} 2' in text
    assert 'honcho_workspace_sessions{state="active",workspace="alpha"} 3' in text
    assert (
        'honcho_workspace_documents{deleted="false",level="explicit",workspace="alpha"} 5' in text
    )
    assert (
        'honcho_workspace_queue_items{state="in_progress",task_type="summary",workspace="alpha"} 1'
        in text
    )
    assert "honcho_exporter_up 1" in text
    assert "honcho_exporter_last_success_timestamp_seconds 100.0" in text


def test_render_metrics_never_emits_unbounded_data_or_error_details() -> None:
    text = render_metrics(
        [WorkspaceSnapshot(workspace="safe-workspace")],
        success_timestamp=0.0,
        duration=0.0,
        healthy=False,
    )
    assert "safe-workspace" in text
    assert "session_name" not in text
    assert "database_dsn" not in text
    assert "honcho_exporter_up 0" in text


def test_config_rejects_database_dsn(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"database_dsn": "postgresql://secret"}')
    with pytest.raises(ConfigurationError, match="unknown configuration"):
        load_config(config)


def test_config_allows_discovery(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"listen": "127.0.0.1", "port": 9477}')
    assert load_config(config).allowed_workspaces is None


class Reader:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def collect(self, workspaces: tuple[str, ...] | None) -> list[WorkspaceSnapshot]:
        if self.fail:
            raise DatabaseError("safe failure")
        return [WorkspaceSnapshot(workspace="alpha")]


def test_health_requires_a_successful_snapshot() -> None:
    state = ExporterState(Reader(), load_config_from_defaults())
    handler = type("TestHandler", (MetricsHandler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.request("GET", "/healthz")
        assert connection.getresponse().status == 503
        connection.request("GET", "/metrics")
        assert connection.getresponse().status == 200
        connection.request("GET", "/healthz")
        assert connection.getresponse().status == 200
    finally:
        server.shutdown()
        thread.join()


def load_config_from_defaults():
    from honcho_prometheus_exporter.config import Config

    return Config()
