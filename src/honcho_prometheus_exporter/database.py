from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable

import psycopg
from psycopg.rows import dict_row

from .collector import WorkspaceSnapshot

DOCUMENT_LEVELS = ("explicit", "deductive", "inductive", "contradiction")
SYNC_STATES = ("synced", "pending", "failed")
TASK_TYPES = ("webhook", "summary", "representation", "dream", "deletion", "reconciler")
QUEUE_STATES = ("pending", "in_progress", "completed")


class DatabaseError(RuntimeError):
    """A safe, non-secret-bearing database collection failure."""


def database_dsn_from_environment() -> str:
    dsn = os.environ.get("HONCHO_EXPORTER_DATABASE_DSN")
    if not dsn:
        raise DatabaseError("HONCHO_EXPORTER_DATABASE_DSN is required")
    return dsn


class PostgresSnapshotReader:
    """Executes aggregate-only SELECT queries inside a read-only transaction."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def collect(self, workspaces: tuple[str, ...] | None) -> list[WorkspaceSnapshot]:
        try:
            with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=5) as connection:
                connection.set_read_only(True)
                with connection.cursor() as cursor:
                    selected = self._workspaces(cursor, workspaces)
                    snapshots = {name: WorkspaceSnapshot(workspace=name) for name in selected}
                    if not snapshots:
                        return []
                    self._populate(cursor, snapshots)
                    return list(snapshots.values())
        except psycopg.Error as exc:
            raise DatabaseError("PostgreSQL snapshot query failed") from exc

    @staticmethod
    def _workspaces(
        cursor: psycopg.Cursor[dict[str, object]], allowed: tuple[str, ...] | None
    ) -> list[str]:
        if allowed is not None:
            cursor.execute(
                "SELECT name FROM workspaces WHERE name = ANY(%s) ORDER BY name", (list(allowed),)
            )
        else:
            cursor.execute("SELECT name FROM workspaces ORDER BY name")
        return [str(row["name"]) for row in cursor.fetchall()]

    @staticmethod
    def _replace(snapshot: WorkspaceSnapshot, **changes: object) -> WorkspaceSnapshot:
        values = snapshot.__dict__ | changes
        return WorkspaceSnapshot(**values)

    def _populate(
        self, cursor: psycopg.Cursor[dict[str, object]], snapshots: dict[str, WorkspaceSnapshot]
    ) -> None:
        names = list(snapshots)
        queries: Iterable[tuple[str, str]] = (
            (
                "peers",
                "SELECT workspace_name, COUNT(*) value FROM peers WHERE workspace_name = ANY(%s) GROUP BY 1",
            ),
            (
                "collections",
                "SELECT workspace_name, COUNT(*) value FROM collections WHERE workspace_name = ANY(%s) GROUP BY 1",
            ),
            (
                "messages",
                "SELECT workspace_name, COUNT(*) value, COALESCE(SUM(token_count), 0) tokens FROM messages WHERE workspace_name = ANY(%s) GROUP BY 1",
            ),
            (
                "sessions",
                "SELECT workspace_name, COUNT(*) FILTER (WHERE is_active) active, COUNT(*) FILTER (WHERE NOT is_active) inactive FROM sessions WHERE workspace_name = ANY(%s) GROUP BY 1",
            ),
            (
                "documents",
                "SELECT workspace_name, level, (deleted_at IS NOT NULL) deleted, COUNT(*) value, COALESCE(SUM(times_derived), 0) derived_sum, COALESCE(MAX(times_derived), 0) derived_max FROM documents WHERE workspace_name = ANY(%s) GROUP BY 1, 2, 3",
            ),
            (
                "message_embeddings",
                "SELECT workspace_name, sync_state, COUNT(*) value FROM message_embeddings WHERE workspace_name = ANY(%s) GROUP BY 1, 2",
            ),
            (
                "document_embeddings",
                "SELECT workspace_name, sync_state, COUNT(*) value FROM documents WHERE workspace_name = ANY(%s) GROUP BY 1, 2",
            ),
            (
                "queue",
                "SELECT q.workspace_name, q.task_type, CASE WHEN q.processed THEN 'completed' WHEN a.work_unit_key IS NOT NULL THEN 'in_progress' ELSE 'pending' END state, COUNT(*) value FROM queue q LEFT JOIN active_queue_sessions a ON a.work_unit_key = q.work_unit_key WHERE q.workspace_name = ANY(%s) GROUP BY 1, 2, 3",
            ),
            (
                "oldest",
                "SELECT q.workspace_name, q.task_type, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - MIN(q.created_at))) age FROM queue q LEFT JOIN active_queue_sessions a ON a.work_unit_key = q.work_unit_key WHERE q.workspace_name = ANY(%s) AND NOT q.processed AND a.work_unit_key IS NULL GROUP BY 1, 2",
            ),
            (
                "active_queue_sessions",
                "SELECT q.workspace_name, COUNT(DISTINCT a.work_unit_key) value FROM active_queue_sessions a JOIN queue q ON q.work_unit_key = a.work_unit_key WHERE q.workspace_name = ANY(%s) GROUP BY 1",
            ),
        )
        documents: dict[str, dict[str, int]] = defaultdict(dict)
        message_embeddings: dict[str, dict[str, int]] = defaultdict(dict)
        document_embeddings: dict[str, dict[str, int]] = defaultdict(dict)
        queue_items: dict[str, dict[str, int]] = defaultdict(dict)
        oldest: dict[str, dict[str, float]] = defaultdict(dict)
        basic: dict[str, dict[str, int]] = defaultdict(dict)
        for kind, query in queries:
            cursor.execute(query, (names,))
            for row in cursor.fetchall():
                workspace = str(row["workspace_name"])
                if kind == "documents":
                    documents[workspace][f"{row['level']}|{str(bool(row['deleted'])).lower()}"] = (
                        int(row["value"])
                    )
                    basic[workspace]["derived_times_sum"] = basic[workspace].get(
                        "derived_times_sum", 0
                    ) + int(row["derived_sum"])
                    basic[workspace]["derived_times_max"] = max(
                        basic[workspace].get("derived_times_max", 0), int(row["derived_max"])
                    )
                elif kind in {"message_embeddings", "document_embeddings"}:
                    target = (
                        message_embeddings if kind == "message_embeddings" else document_embeddings
                    )
                    target[workspace][str(row["sync_state"])] = int(row["value"])
                elif kind == "queue":
                    queue_items[workspace][f"{row['task_type']}|{row['state']}"] = int(row["value"])
                elif kind == "oldest":
                    oldest[workspace][str(row["task_type"])] = float(row["age"])
                elif kind == "sessions":
                    basic[workspace]["sessions_active"] = int(row["active"])
                    basic[workspace]["sessions_inactive"] = int(row["inactive"])
                elif kind == "messages":
                    basic[workspace]["messages"] = int(row["value"])
                    basic[workspace]["message_tokens"] = int(row["tokens"])
                else:
                    basic[workspace][kind] = int(row["value"])
        for workspace, snapshot in list(snapshots.items()):
            snapshots[workspace] = self._replace(
                snapshot,
                **basic[workspace],
                documents=documents[workspace],
                message_embeddings=message_embeddings[workspace],
                document_embeddings=document_embeddings[workspace],
                queue_items=queue_items[workspace],
                oldest_pending_age=oldest[workspace],
            )
