#!/usr/bin/env python3
"""Upload a local FinBERT checkpoint as a deployable S3 model artifact."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from utils.aws import aws_client
from utils.common import load_local_env, persist_env_values, resolve_region
from utils.finbert_artifacts import (
    model_artifact_base_key,
    model_artifact_transfer_config,
    model_artifact_uri,
    selected_model_files,
    upload_model_file,
)

AWS_REGION = "us-east-1"
DEFAULT_MODEL_DIR = "../artifacts/finbert/checkpoint-9700"
DEFAULT_BUCKET = "proyecto-integrador-2-features-amce"
DEFAULT_PREFIX = "models/finbert/manual"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a deployable FinBERT model artifact to S3.")
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--force-upload", action="store_true", help="Upload even when FINBERT_ARTIFACT_URI already exists in S3.")
    return parser.parse_args()


def existing_artifact_uri() -> str:
    load_local_env()
    return os.getenv("FINBERT_ARTIFACT_URI", "").strip()


def parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.strip("/")


def s3_object_exists(s3_client, *, bucket: str, key: str) -> bool:
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as error:
        error_response = getattr(error, "response", {})
        error_code = error_response.get("Error", {}).get("Code")
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def artifact_files_exist(s3_client, *, artifact_uri: str, files: list[Path]) -> bool:
    bucket, prefix = parse_s3_uri(artifact_uri)
    base_prefix = prefix.rstrip("/")
    for path in files:
        key = f"{base_prefix}/{path.name}" if base_prefix else path.name
        if not s3_object_exists(s3_client, bucket=bucket, key=key):
            return False
    return True


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


def upload_file(s3_client, *, path: Path, bucket: str, key: str, config) -> None:
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
    upload_model_file(
        s3_client,
        path=path,
        bucket=bucket,
        key=key,
        callback=callback,
        config=config,
    )
    sys.stdout.write(progress_bar(filename=path.name, uploaded=total, total=total, started_at=started_at))
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> int:
    args = parse_args()
    model_dir = Path(args.model_dir).resolve()
    files = selected_model_files(model_dir)
    total_mib = sum(path.stat().st_size for path in files) / 1024 / 1024

    s3_client = aws_client("s3", region=resolve_region(AWS_REGION))
    cached_artifact_uri = existing_artifact_uri()
    if cached_artifact_uri and not args.force_upload:
        print(f"Checking existing model artifact: {cached_artifact_uri}")
        if artifact_files_exist(s3_client, artifact_uri=cached_artifact_uri, files=files):
            print(f"Existing model artifact is complete. Skipping upload: {cached_artifact_uri}")
            print(f"artifact_uri={cached_artifact_uri}")
            return 0
        print("Existing model artifact is incomplete or missing. Uploading a fresh artifact.")

    base_key = model_artifact_base_key(args.prefix, run_id=args.run_id or None)
    artifact_uri = model_artifact_uri(bucket=args.bucket, base_key=base_key)
    print(f"Uploading model artifact from: {model_dir}")
    print(f"Target artifact: {artifact_uri}")
    print(f"Files: {len(files)} ({total_mib:.1f} MiB total)")

    config = model_artifact_transfer_config(max_concurrency=4)
    for path in files:
        upload_file(
            s3_client,
            path=path,
            bucket=args.bucket,
            key=f"{base_key}/{path.name}",
            config=config,
        )

    persist_env_values({"FINBERT_ARTIFACT_URI": artifact_uri})
    print(f"artifact_uri={artifact_uri}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
