"""SSH command helpers for infra scripts."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def ssh_base(*, key_path: str) -> list[str]:
    command = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ConnectTimeout=10",
    ]
    if key_path:
        command.extend(["-i", key_path])
    return command


def scp_base(*, key_path: str) -> list[str]:
    command = [
        "scp",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ConnectTimeout=10",
    ]
    if key_path:
        command.extend(["-i", key_path])
    return command


def remote(host: str, user: str, key_path: str, command: str) -> None:
    run([*ssh_base(key_path=key_path), f"{user}@{host}", command])
