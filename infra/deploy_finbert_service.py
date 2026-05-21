#!/usr/bin/env python3
"""Deploy the FinBERT API service to the shared EC2 instance over SSH."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig

from utils.common import require_env, resolve_region

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
MODEL_FILES = {
    "config.json",
    "model.safetensors",
    "pytorch_model.bin",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
    "merges.txt",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy the FinBERT FastAPI service to EC2.")
    parser.add_argument("--artifact-uri", default="")
    parser.add_argument("--local-model-dir", default=DEFAULT_LOCAL_MODEL_DIR)
    parser.add_argument("--artifact-bucket", default=DEFAULT_ARTIFACT_BUCKET)
    parser.add_argument("--artifact-prefix", default=DEFAULT_ARTIFACT_PREFIX)
    parser.add_argument("--model-name", default="ProsusAI/finbert")
    parser.add_argument("--ssh-user", default="ec2-user")
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


def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def ssh_base(*, key_path: str) -> list[str]:
    command = ["ssh", "-o", "StrictHostKeyChecking=accept-new"]
    if key_path:
        command.extend(["-i", key_path])
    return command


def scp_base(*, key_path: str) -> list[str]:
    command = ["scp", "-o", "StrictHostKeyChecking=accept-new"]
    if key_path:
        command.extend(["-i", key_path])
    return command


def find_instance_host() -> str:
    ec2_client = boto3.Session(region_name=resolve_region(AWS_REGION)).client("ec2")
    reservations = ec2_client.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [INSTANCE_NAME]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )["Reservations"]
    instances = [instance for reservation in reservations for instance in reservation["Instances"]]
    if not instances:
        raise RuntimeError(f"No running FinBERT EC2 instance found with Name={INSTANCE_NAME}.")
    return instances[0].get("PublicDnsName") or instances[0]["PublicIpAddress"]


def remote(host: str, user: str, key_path: str, command: str) -> None:
    run([*ssh_base(key_path=key_path), f"{user}@{host}", command])


def selected_model_files(model_dir: Path) -> list[Path]:
    if not model_dir.exists():
        raise FileNotFoundError(f"Local model directory does not exist: {model_dir}")

    selected_files = [path for path in sorted(model_dir.iterdir()) if path.is_file() and path.name in MODEL_FILES]
    selected_names = {path.name for path in selected_files}
    if "config.json" not in selected_names:
        raise FileNotFoundError(f"Missing config.json in local model dir: {model_dir}")
    if not ({"model.safetensors", "pytorch_model.bin"} & selected_names):
        raise FileNotFoundError(f"Missing model weights in local model dir: {model_dir}")
    if not ({"tokenizer.json", "vocab.txt"} & selected_names):
        raise FileNotFoundError(f"Missing tokenizer files in local model dir: {model_dir}")
    return selected_files


def upload_local_model_to_s3(*, model_dir: Path, bucket: str, prefix: str) -> str:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base_key = f"{prefix.strip('/')}/run_id={run_id}"
    s3_client = boto3.client("s3")
    transfer_config = TransferConfig(multipart_threshold=8 * 1024 * 1024, multipart_chunksize=8 * 1024 * 1024)
    for path in selected_model_files(model_dir):
        total_bytes = path.stat().st_size
        uploaded_bytes = 0
        started_at = time.monotonic()
        last_reported_at = 0.0

        def report_progress(chunk_bytes: int) -> None:
            nonlocal uploaded_bytes, last_reported_at
            uploaded_bytes += chunk_bytes
            now = time.monotonic()
            if now - last_reported_at < 5 and uploaded_bytes < total_bytes:
                return
            last_reported_at = now
            elapsed = max(now - started_at, 0.001)
            mib_done = uploaded_bytes / 1024 / 1024
            mib_total = total_bytes / 1024 / 1024
            mib_per_second = mib_done / elapsed
            print(f"  {path.name}: {mib_done:.1f}/{mib_total:.1f} MiB at {mib_per_second:.2f} MiB/s")
            sys.stdout.flush()

        print(f"Uploading {path.name} ({total_bytes / 1024 / 1024:.1f} MiB) to s3://{bucket}/{base_key}/{path.name}")
        sys.stdout.flush()
        s3_client.upload_file(
            str(path),
            bucket,
            f"{base_key}/{path.name}",
            Callback=report_progress,
            Config=transfer_config,
        )
        print(f"Uploaded {path.name}")
        sys.stdout.flush()
    artifact_uri = f"s3://{bucket}/{base_key}/"
    print(f"Uploaded local model artifact to: {artifact_uri}")
    return artifact_uri


def default_requirements_file(python_version: str) -> str:
    if python_version.startswith("3.7"):
        return "requirements-ec2.txt"
    return "requirements-ec2-modern.txt"


if __name__ == "__main__":
    args = parse_args()
    key_path = args.key_path or require_env("EC2_KEY_PATH")
    host = args.host or find_instance_host()
    user_host = f"{args.ssh_user}@{host}"
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
        artifact_uri = upload_local_model_to_s3(
            model_dir=local_model_dir,
            bucket=args.artifact_bucket,
            prefix=args.artifact_prefix,
        )
    try:
        remote(host, args.ssh_user, key_path, "sudo mkdir -p /opt/finbert && sudo chown -R $USER:$USER /opt/finbert")
        run([*scp_base(key_path=key_path), str(archive_path), f"{user_host}:/tmp/finbert-service-src.tar.gz"])
        remote(
            host,
            args.ssh_user,
            key_path,
            (
                f"rm -rf {REMOTE_DIR} && mkdir -p {REMOTE_DIR} "
                f"&& tar -xzf /tmp/finbert-service-src.tar.gz -C {REMOTE_DIR}"
            ),
        )
        remote(
            host,
            args.ssh_user,
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
        remote(host, args.ssh_user, key_path, f"sudo rm -rf {MODEL_DIR} && sudo mkdir -p {MODEL_DIR} && sudo chown -R $USER:$USER /opt/finbert")
        if artifact_uri:
            remote(
                host,
                args.ssh_user,
                key_path,
                (
                    f"aws s3 sync '{artifact_uri}' '{MODEL_DIR}/' --delete --only-show-errors "
                    f"&& test -f '{MODEL_DIR}/config.json' "
                    f"&& (test -f '{MODEL_DIR}/model.safetensors' || test -f '{MODEL_DIR}/pytorch_model.bin')"
                ),
            )
        remote(
            host,
            args.ssh_user,
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
            args.ssh_user,
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
            args.ssh_user,
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
            args.ssh_user,
            key_path,
            "sudo systemctl daemon-reload && sudo systemctl enable finbert-api.service && sudo systemctl restart finbert-api.service",
        )
        remote(
            host,
            args.ssh_user,
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
