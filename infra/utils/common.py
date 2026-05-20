"""Common helpers for AWS provisioning scripts."""

from __future__ import annotations

import os
from pathlib import Path


_ENV_LOADED = False


def load_local_env() -> None:
    """Load infra/.env into os.environ without overriding exported values."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name:
            os.environ.setdefault(name, value)


def resolve_region(default_region: str) -> str:
    load_local_env()
    return os.getenv("AWS_REGION", default_region)


def require_env(name: str, *, placeholder_prefixes: tuple[str, ...] = ()) -> str:
    load_local_env()
    value = os.getenv(name, "").strip()
    if not value or any(value.startswith(prefix) for prefix in placeholder_prefixes):
        raise ValueError(f"Set the {name} environment variable before running this script.")
    return value


def require_csv_env(
    name: str,
    *,
    min_values: int = 1,
    placeholder_prefixes: tuple[str, ...] = (),
) -> list[str]:
    values = [value.strip() for value in require_env(name).split(",") if value.strip()]
    if len(values) < min_values or any(
        any(value.startswith(prefix) for prefix in placeholder_prefixes) for value in values
    ):
        raise ValueError(
            f"Set the {name} environment variable to at least {min_values} comma-separated values."
        )
    return values


def serialize_tags(tags: dict[str, str]) -> list[dict[str, str]]:
    return [{"Key": key, "Value": value} for key, value in tags.items()]
