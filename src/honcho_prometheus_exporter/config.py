from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    listen: str = "127.0.0.1"
    port: int = 9477
    allowed_workspaces: tuple[str, ...] | None = None


def load_config(path: Path) -> Config:
    try:
        raw: Any = json.loads(path.read_text())
    except OSError as exc:
        raise ConfigurationError("could not read configuration") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError("configuration is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("configuration root must be an object")
    unknown = set(raw) - {"listen", "port", "allowed_workspaces"}
    if unknown:
        raise ConfigurationError(f"unknown configuration key(s): {', '.join(sorted(unknown))}")
    listen = raw.get("listen", "127.0.0.1")
    port = raw.get("port", 9477)
    allowed = raw.get("allowed_workspaces")
    if not isinstance(listen, str) or not listen:
        raise ConfigurationError("listen must be a non-empty string")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigurationError("port must be an integer from 1 to 65535")
    if allowed is None:
        workspaces = None
    elif isinstance(allowed, list) and all(isinstance(item, str) and item for item in allowed):
        if len(set(allowed)) != len(allowed):
            raise ConfigurationError("allowed_workspaces must not contain duplicates")
        workspaces = tuple(allowed)
    else:
        raise ConfigurationError("allowed_workspaces must be an array of non-empty strings")
    return Config(listen=listen, port=port, allowed_workspaces=workspaces)
