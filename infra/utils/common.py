"""Common helpers for AWS provisioning scripts."""

from __future__ import annotations

import os
import json
import secrets
import string
import tempfile
from pathlib import Path


_ENV_LOADED = False


def env_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".env"


def _parse_env_assignment(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    name, value = line.split("=", 1)
    name = name.strip()
    if not name:
        return None
    value = value.strip().strip('"').strip("'")
    return name, value


def read_env_file(path: Path | None = None) -> tuple[list[str], dict[str, str]]:
    path = path or env_path()
    if not path.exists():
        return [], {}

    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for raw_line in lines:
        assignment = _parse_env_assignment(raw_line)
        if assignment:
            name, value = assignment
            values[name] = value
    return lines, values


def write_env_values(values: dict[str, str], *, path: Path | None = None) -> None:
    path = path or env_path()
    lines, _ = read_env_file(path)
    pending = {key: str(value) for key, value in values.items()}
    updated_lines: list[str] = []

    for raw_line in lines:
        assignment = _parse_env_assignment(raw_line)
        if not assignment:
            updated_lines.append(raw_line)
            continue

        name, _ = assignment
        if name in pending:
            updated_lines.append(f"{name}={pending.pop(name)}")
        else:
            updated_lines.append(raw_line)

    if pending and updated_lines and updated_lines[-1].strip():
        updated_lines.append("")
    for name, value in pending.items():
        updated_lines.append(f"{name}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(updated_lines)
    if payload:
        payload += "\n"

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as temp_file:
        temp_file.write(payload)
        temp_path = Path(temp_file.name)
    temp_path.replace(path)


def set_runtime_env(values: dict[str, str]) -> None:
    for name, value in values.items():
        os.environ[name] = str(value)


def persist_env_values(
    values: dict[str, str],
    *,
    secret_keys: set[str] | frozenset[str] = frozenset(),
) -> None:
    sanitized = {name: str(value) for name, value in values.items() if value is not None}
    if not sanitized:
        return
    write_env_values(sanitized)
    set_runtime_env(sanitized)
    keys = ", ".join(sanitized)
    print(f"Updated infra/.env: {keys}")


def generate_secret(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def load_local_env() -> None:
    """Load infra/.env into os.environ without overriding exported values."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    _, values = read_env_file()
    for name, value in values.items():
        if value:
            os.environ[name] = value
        elif name not in os.environ:
            os.environ[name] = value


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


def load_json_document(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(payload)


def ensure(check, create, *, update=None, setup=None):
    """Return an existing or created resource after optional reconciliation and setup."""
    resource = check()
    if resource is None:
        resource = create()
    elif update:
        resource = update(resource)
    if setup:
        return setup(resource)
    return resource
