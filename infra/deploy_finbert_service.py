#!/usr/bin/env python3
"""Deploy the FinBERT API service to the shared EC2 instance over SSH."""

from __future__ import annotations

import argparse
import os
import socket
from pathlib import Path

from utils.aws import aws_client
from utils.common import load_local_env, require_env, resolve_region
from utils.ec2 import require_instance_by_name
from utils.finbert_artifacts import upload_model_artifact
from utils.ssh import remote, run, scp_base

AWS_REGION = "us-east-1"
INSTANCE_NAME = "proyecto-finbert-ec2"
REMOTE_DIR = "/opt/finbert/app"
MODEL_DIR = "/opt/finbert/model"
VENV_DIR = "/opt/finbert/venv"
ENV_FILE = "/etc/finbert-api.env"
SERVICE_FILE = "/etc/systemd/system/finbert-api.service"
DEFAULT_PYTHON_VERSION = "3.13.13"
DEFAULT_LOCAL_MODEL_DIR = "artifacts/finbert/checkpoint-9700"
DEFAULT_ARTIFACT_BUCKET = "proyecto-integrador-2-features-amce"
DEFAULT_ARTIFACT_PREFIX = "models/finbert/manual"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy the FinBERT FastAPI service to EC2.")
    parser.add_argument("--artifact-uri", default="")
    parser.add_argument("--local-model-dir", default=DEFAULT_LOCAL_MODEL_DIR)
    parser.add_argument("--artifact-bucket", default=DEFAULT_ARTIFACT_BUCKET)
    parser.add_argument("--artifact-prefix", default=DEFAULT_ARTIFACT_PREFIX)
    parser.add_argument("--model-name", default="ProsusAI/finbert")
    parser.add_argument("--ssh-user", default="")
    parser.add_argument("--key-path", default="")
    parser.add_argument("--host", default="")
    parser.add_argument(
        "--python-version",
        default=DEFAULT_PYTHON_VERSION,
        help="Python version uv should install on EC2 for the service venv.",
    )
    parser.add_argument(
        "--requirements-file",
        default="",
        help="Requirements file under finbert/. Defaults to modern requirements for Python >=3.8.",
    )
    return parser.parse_args()


def running_instance() -> dict:
    ec2_client = aws_client("ec2", region=resolve_region(AWS_REGION))
    return require_instance_by_name(
        ec2_client,
        name=INSTANCE_NAME,
        states=["running"],
        message=f"No running FinBERT EC2 instance found with Name={INSTANCE_NAME}.",
    )


def find_instance_host() -> str:
    instance = running_instance()
    return instance.get("PublicDnsName") or instance["PublicIpAddress"]


def optional_env(name: str) -> str:
    load_local_env()
    return os.getenv(name, "").strip()


def resolve_host(args: argparse.Namespace) -> str:
    return args.host or optional_env("FINBERT_PUBLIC_DNS") or optional_env("FINBERT_PUBLIC_IP") or find_instance_host()


def resolve_ssh_user(args: argparse.Namespace) -> str:
    return args.ssh_user or optional_env("FINBERT_SSH_USER") or "ec2-user"


def check_ssh_port(host: str, *, timeout_seconds: int = 10) -> None:
    try:
        with socket.create_connection((host, 22), timeout=timeout_seconds):
            return
    except OSError as error:
        raise SystemExit(
            f"Could not connect to SSH on {host}:22 within {timeout_seconds} seconds. "
            "This is a network/security-group problem, not an SSH key problem. "
            "Run `python create_security_groups.py` to refresh SSH_ALLOWED_CIDR for your current public IP, "
            "then rerun the deploy."
        ) from error


def validate_instance_key_name() -> None:
    expected_key_name = require_env("EC2_KEY_NAME")
    instance = running_instance()
    actual_key_name = instance.get("KeyName", "")
    if actual_key_name and actual_key_name != expected_key_name:
        raise SystemExit(
            f"The running EC2 instance was launched with key pair {actual_key_name!r}, "
            f"but infra/.env has EC2_KEY_NAME={expected_key_name!r}. "
            "Changing EC2_KEY_NAME or recreating/downloading a local .pem does not update an existing instance. "
            "Use the private key for the original key pair, or terminate/recreate the FinBERT EC2 instance "
            "with the key pair you want to use."
        )


def default_requirements_file(python_version: str) -> str:
    if python_version.startswith("3.7"):
        return "requirements-ec2.txt"
    return "requirements-ec2-modern.txt"


if __name__ == "__main__":
    args = parse_args()
    key_path = args.key_path or require_env("EC2_KEY_PATH")
    host = resolve_host(args)
    ssh_user = resolve_ssh_user(args)
    validate_instance_key_name()
    check_ssh_port(host)
    user_host = f"{ssh_user}@{host}"
    repo_root = Path(__file__).resolve().parents[1]
    archive_path = repo_root / "finbert-service-src.tar.gz"
    local_model_dir = (repo_root / args.local_model_dir).resolve()
    artifact_uri = args.artifact_uri
    requirements_file = args.requirements_file or default_requirements_file(args.python_version)

    run(
        [
            "tar",
            "--exclude=.git",
            "--exclude=.venv",
            "--exclude=artifacts",
            "--exclude=data",
            "-czf",
            str(archive_path),
            "finbert",
            "utils",
        ],
        cwd=repo_root,
    )
    if not artifact_uri and args.local_model_dir:
        s3_client = aws_client("s3", region=resolve_region(AWS_REGION))
        artifact_uri = upload_model_artifact(
            s3_client,
            model_dir=local_model_dir,
            bucket=args.artifact_bucket,
            prefix=args.artifact_prefix,
        )
    try:
        remote(host, ssh_user, key_path, "sudo mkdir -p /opt/finbert && sudo chown -R $USER:$USER /opt/finbert")
        run([*scp_base(key_path=key_path), str(archive_path), f"{user_host}:/tmp/finbert-service-src.tar.gz"])
        remote(
            host,
            ssh_user,
            key_path,
            (
                f"rm -rf {REMOTE_DIR} && mkdir -p {REMOTE_DIR} "
                f"&& tar -xzf /tmp/finbert-service-src.tar.gz -C {REMOTE_DIR}"
            ),
        )
        remote(
            host,
            ssh_user,
            key_path,
            (
                "sudo yum install -y awscli curl tar gzip "
                "&& if [ ! -x \"$HOME/.local/bin/uv\" ]; then curl -LsSf https://astral.sh/uv/install.sh | sh; fi "
                f"&& $HOME/.local/bin/uv python install {args.python_version} "
                f"&& rm -rf {VENV_DIR} "
                f"&& $HOME/.local/bin/uv venv --python {args.python_version} {VENV_DIR} "
                f"&& $HOME/.local/bin/uv pip install --only-binary :all: --index-strategy unsafe-best-match "
                f"--python {VENV_DIR}/bin/python -r {REMOTE_DIR}/finbert/{requirements_file} "
                f"&& {VENV_DIR}/bin/python --version"
            ),
        )
        remote(host, ssh_user, key_path, f"sudo rm -rf {MODEL_DIR} && sudo mkdir -p {MODEL_DIR} && sudo chown -R $USER:$USER /opt/finbert")
        if artifact_uri:
            remote(
                host,
                ssh_user,
                key_path,
                (
                    f"aws s3 sync '{artifact_uri}' '{MODEL_DIR}/' --delete --only-show-errors "
                    f"&& test -f '{MODEL_DIR}/config.json' "
                    f"&& (test -f '{MODEL_DIR}/model.safetensors' || test -f '{MODEL_DIR}/pytorch_model.bin')"
                ),
            )
        remote(
            host,
            ssh_user,
            key_path,
            (
                "if [ ! -f /swapfile ]; then "
                "sudo fallocate -l 3G /swapfile "
                "&& sudo chmod 600 /swapfile "
                "&& sudo mkswap /swapfile "
                "&& sudo swapon /swapfile "
                "&& echo '/swapfile swap swap defaults 0 0' | sudo tee -a /etc/fstab >/dev/null; "
                "else sudo swapon /swapfile || true; fi"
            ),
        )
        remote(
            host,
            ssh_user,
            key_path,
            (
                "sudo tee "
                f"{ENV_FILE} >/dev/null <<'EOF'\n"
                f"FINBERT_ARTIFACT_URI={artifact_uri}\n"
                f"FINBERT_MODEL_NAME={args.model_name}\n"
                f"FINBERT_MODEL_DIR={MODEL_DIR}\n"
                "FINBERT_HOST=0.0.0.0\n"
                "FINBERT_PORT=8000\n"
                "FINBERT_MAX_BATCH_SIZE=32\n"
                "PYTHONPATH=/opt/finbert/app\n"
                "EOF"
            ),
        )
        remote(
            host,
            ssh_user,
            key_path,
            (
                "sudo tee "
                f"{SERVICE_FILE} >/dev/null <<'EOF'\n"
                "[Unit]\n"
                "Description=FinBERT Sentiment API\n"
                "After=network-online.target\n"
                "Wants=network-online.target\n\n"
                "[Service]\n"
                "Type=simple\n"
                f"WorkingDirectory={REMOTE_DIR}\n"
                f"EnvironmentFile={ENV_FILE}\n"
                f"ExecStart={VENV_DIR}/bin/python -m uvicorn finbert.service:app --host ${{FINBERT_HOST}} --port ${{FINBERT_PORT}}\n"
                "Restart=on-failure\n"
                "RestartSec=5\n\n"
                "[Install]\n"
                "WantedBy=multi-user.target\n"
                "EOF"
            ),
        )
        remote(
            host,
            ssh_user,
            key_path,
            "sudo systemctl daemon-reload && sudo systemctl enable finbert-api.service && sudo systemctl restart finbert-api.service",
        )
        remote(
            host,
            ssh_user,
            key_path,
            (
                "systemctl is-active finbert-api.service "
                "&& for attempt in $(seq 1 36); do "
                "curl -fsS http://localhost:8000/health && exit 0; "
                "sleep 5; "
                "done; "
                "sudo journalctl -u finbert-api.service --no-pager -n 80; "
                "exit 1"
            ),
        )
        print(f"FinBERT API deployed: http://{host}:8000")
    finally:
        archive_path.unlink(missing_ok=True)
