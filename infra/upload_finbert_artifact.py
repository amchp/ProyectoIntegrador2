#!/usr/bin/env python3
"""Upload a local FinBERT checkpoint as a deployable S3 model artifact."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig

from utils.common import resolve_region

AWS_REGION = "us-east-1"
DEFAULT_MODEL_DIR = "../artifacts/finbert/checkpoint-9700"
DEFAULT_BUCKET = "proyecto-integrador-2-features-amce"
DEFAULT_PREFIX = "models/finbert/manual"
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
    parser = argparse.ArgumentParser(description="Upload a deployable FinBERT model artifact to S3.")
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--run-id", default="")
    return parser.parse_args()


def selected_model_files(model_dir: Path) -> list[Path]:
    if not model_dir.exists():
        raise FileNotFoundError(f"Model directory does not exist: {model_dir}")

    selected = [path for path in sorted(model_dir.iterdir()) if path.is_file() and path.name in MODEL_FILES]
    names = {path.name for path in selected}
    if "config.json" not in names:
        raise FileNotFoundError(f"Missing config.json in {model_dir}")
    if not ({"model.safetensors", "pytorch_model.bin"} & names):
        raise FileNotFoundError(f"Missing model weights in {model_dir}")
    if not ({"tokenizer.json", "vocab.txt"} & names):
        raise FileNotFoundError(f"Missing tokenizer files in {model_dir}")
    return selected


def progress_bar(*, filename: str, uploaded: int, total: int, started_at: float) -> str:
    width = 32
    ratio = min(uploaded / total, 1.0) if total else 1.0
    done = int(width * ratio)
    bar = "#" * done + "-" * (width - done)
    elapsed = max(time.monotonic() - started_at, 0.001)
    mib_done = uploaded / 1024 / 1024
    mib_total = total / 1024 / 1024
    speed = mib_done / elapsed
    return f"\r{filename:24} [{bar}] {ratio * 100:6.2f}% {mib_done:8.1f}/{mib_total:8.1f} MiB {speed:6.2f} MiB/s"


def upload_file(s3_client, *, path: Path, bucket: str, key: str, config: TransferConfig) -> None:
    total = path.stat().st_size
    uploaded = 0
    started_at = time.monotonic()
    last_draw = 0.0

    def callback(chunk_bytes: int) -> None:
        nonlocal uploaded, last_draw
        uploaded += chunk_bytes
        now = time.monotonic()
        if now - last_draw < 0.2 and uploaded < total:
            return
        last_draw = now
        sys.stdout.write(progress_bar(filename=path.name, uploaded=uploaded, total=total, started_at=started_at))
        sys.stdout.flush()

    sys.stdout.write(progress_bar(filename=path.name, uploaded=0, total=total, started_at=started_at))
    sys.stdout.flush()
    s3_client.upload_file(str(path), bucket, key, Callback=callback, Config=config)
    sys.stdout.write(progress_bar(filename=path.name, uploaded=total, total=total, started_at=started_at))
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> int:
    args = parse_args()
    model_dir = Path(args.model_dir).resolve()
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base_key = f"{args.prefix.strip('/')}/run_id={run_id}"
    artifact_uri = f"s3://{args.bucket}/{base_key}/"
    files = selected_model_files(model_dir)
    total_mib = sum(path.stat().st_size for path in files) / 1024 / 1024

    print(f"Uploading model artifact from: {model_dir}")
    print(f"Target artifact: {artifact_uri}")
    print(f"Files: {len(files)} ({total_mib:.1f} MiB total)")

    s3_client = boto3.Session(region_name=resolve_region(AWS_REGION)).client("s3")
    config = TransferConfig(
        multipart_threshold=8 * 1024 * 1024,
        multipart_chunksize=8 * 1024 * 1024,
        max_concurrency=4,
        use_threads=True,
    )
    for path in files:
        upload_file(
            s3_client,
            path=path,
            bucket=args.bucket,
            key=f"{base_key}/{path.name}",
            config=config,
        )

    print(f"artifact_uri={artifact_uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
