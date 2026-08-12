from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class WorkspaceSnapshot:
    """Aggregated, count-only data for one allowed workspace."""

    workspace: str
    peers: int = 0
    sessions_active: int = 0
    sessions_inactive: int = 0
    messages: int = 0
    message_tokens: int = 0
    collections: int = 0
    documents: dict[str, int] = field(default_factory=dict)
    derived_times_sum: int = 0
    derived_times_max: int = 0
    message_embeddings: dict[str, int] = field(default_factory=dict)
    document_embeddings: dict[str, int] = field(default_factory=dict)
    queue_items: dict[str, int] = field(default_factory=dict)
    oldest_pending_age: dict[str, float] = field(default_factory=dict)
    active_queue_sessions: int = 0


class SnapshotReader(Protocol):
    def collect(self, workspaces: tuple[str, ...] | None) -> list[WorkspaceSnapshot]: ...


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _sample(name: str, value: float | int, **labels: object) -> str:
    text = ",".join(f'{key}="{_label(labels[key])}"' for key in sorted(labels))
    return f"{name}{{{text}}} {value}\n" if text else f"{name} {value}\n"


def render_metrics(
    snapshots: list[WorkspaceSnapshot], *, success_timestamp: float, duration: float, healthy: bool
) -> str:
    """Render count-only Prometheus exposition with bounded enumerated labels."""
    types = (
        "honcho_exporter_up",
        "honcho_exporter_last_success_timestamp_seconds",
        "honcho_exporter_scrape_duration_seconds",
        "honcho_workspace_peers",
        "honcho_workspace_sessions",
        "honcho_workspace_messages",
        "honcho_workspace_message_tokens",
        "honcho_workspace_collections",
        "honcho_workspace_documents",
        "honcho_workspace_document_derived_times_sum",
        "honcho_workspace_document_derived_times_max",
        "honcho_workspace_message_embeddings",
        "honcho_workspace_document_embeddings",
        "honcho_workspace_queue_items",
        "honcho_workspace_oldest_pending_age_seconds",
        "honcho_workspace_active_queue_sessions",
    )
    lines = [f"# TYPE {name} gauge\n" for name in types]
    lines.extend(
        (
            _sample("honcho_exporter_up", int(healthy)),
            _sample("honcho_exporter_last_success_timestamp_seconds", success_timestamp),
            _sample("honcho_exporter_scrape_duration_seconds", duration),
        )
    )
    for snapshot in sorted(snapshots, key=lambda item: item.workspace):
        labels = {"workspace": snapshot.workspace}
        lines.extend(
            (
                _sample("honcho_workspace_peers", snapshot.peers, **labels),
                _sample(
                    "honcho_workspace_sessions", snapshot.sessions_active, state="active", **labels
                ),
                _sample(
                    "honcho_workspace_sessions",
                    snapshot.sessions_inactive,
                    state="inactive",
                    **labels,
                ),
                _sample("honcho_workspace_messages", snapshot.messages, **labels),
                _sample("honcho_workspace_message_tokens", snapshot.message_tokens, **labels),
                _sample("honcho_workspace_collections", snapshot.collections, **labels),
                _sample(
                    "honcho_workspace_document_derived_times_sum",
                    snapshot.derived_times_sum,
                    **labels,
                ),
                _sample(
                    "honcho_workspace_document_derived_times_max",
                    snapshot.derived_times_max,
                    **labels,
                ),
                _sample(
                    "honcho_workspace_active_queue_sessions",
                    snapshot.active_queue_sessions,
                    **labels,
                ),
            )
        )
        for level in ("explicit", "deductive", "inductive", "contradiction"):
            for deleted in ("false", "true"):
                count = snapshot.documents.get(f"{level}|{deleted}", 0)
                lines.append(
                    _sample(
                        "honcho_workspace_documents", count, level=level, deleted=deleted, **labels
                    )
                )
        for state in ("synced", "pending", "failed"):
            count = snapshot.message_embeddings.get(state, 0)
            lines.append(
                _sample("honcho_workspace_message_embeddings", count, sync_state=state, **labels)
            )
        for state in ("synced", "pending", "failed"):
            count = snapshot.document_embeddings.get(state, 0)
            lines.append(
                _sample("honcho_workspace_document_embeddings", count, sync_state=state, **labels)
            )
        for task_type in (
            "webhook",
            "summary",
            "representation",
            "dream",
            "deletion",
            "reconciler",
        ):
            for state in ("pending", "in_progress", "completed"):
                count = snapshot.queue_items.get(f"{task_type}|{state}", 0)
                lines.append(
                    _sample(
                        "honcho_workspace_queue_items",
                        count,
                        task_type=task_type,
                        state=state,
                        **labels,
                    )
                )
        for task_type in (
            "webhook",
            "summary",
            "representation",
            "dream",
            "deletion",
            "reconciler",
        ):
            age = snapshot.oldest_pending_age.get(task_type, 0.0)
            lines.append(
                _sample(
                    "honcho_workspace_oldest_pending_age_seconds",
                    age,
                    task_type=task_type,
                    **labels,
                )
            )
    return "".join(lines)
