"""FinBERT model artifact helpers shared by infra commands."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

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


def model_artifact_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def model_artifact_base_key(prefix: str, *, run_id: str | None = None) -> str:
    return f"{prefix.strip('/')}/run_id={run_id or model_artifact_run_id()}"


def model_artifact_uri(*, bucket: str, base_key: str) -> str:
    return f"s3://{bucket}/{base_key}/"


def model_artifact_transfer_config(
    *,
    max_concurrency: int | None = None,
    use_threads: bool = True,
) -> TransferConfig:
    from boto3.s3.transfer import TransferConfig

    kwargs = {
        "multipart_threshold": 8 * 1024 * 1024,
        "multipart_chunksize": 8 * 1024 * 1024,
        "use_threads": use_threads,
    }
    if max_concurrency is not None:
        kwargs["max_concurrency"] = max_concurrency
    return TransferConfig(**kwargs)


def upload_model_file(
    s3_client,
    *,
    path: Path,
    bucket: str,
    key: str,
    config,
    callback=None,
) -> None:
    s3_client.upload_file(
        str(path),
        bucket,
        key,
        Callback=callback,
        Config=config,
    )


def selected_model_files(model_dir: Path) -> list[Path]:
    if not model_dir.exists():
        raise FileNotFoundError(f"Local model directory does not exist: {model_dir}")

    selected_files = [
        path
        for path in sorted(model_dir.iterdir())
        if path.is_file() and path.name in MODEL_FILES
    ]
    selected_names = {path.name for path in selected_files}
    if "config.json" not in selected_names:
        raise FileNotFoundError(f"Missing config.json in local model dir: {model_dir}")
    if not ({"model.safetensors", "pytorch_model.bin"} & selected_names):
        raise FileNotFoundError(f"Missing model weights in local model dir: {model_dir}")
    if not ({"tokenizer.json", "vocab.txt"} & selected_names):
        raise FileNotFoundError(f"Missing tokenizer files in local model dir: {model_dir}")
    return selected_files


def upload_model_artifact(
    s3_client,
    *,
    model_dir: Path,
    bucket: str,
    prefix: str,
) -> str:
    base_key = model_artifact_base_key(prefix)
    transfer_config = model_artifact_transfer_config()
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
            print(
                f"  {path.name}: {mib_done:.1f}/{mib_total:.1f} MiB "
                f"at {mib_per_second:.2f} MiB/s"
            )
            sys.stdout.flush()

        print(
            f"Uploading {path.name} ({total_bytes / 1024 / 1024:.1f} MiB) "
            f"to s3://{bucket}/{base_key}/{path.name}"
        )
        sys.stdout.flush()
        upload_model_file(
            s3_client,
            path=path,
            bucket=bucket,
            key=f"{base_key}/{path.name}",
            callback=report_progress,
            config=transfer_config,
        )
        print(f"Uploaded {path.name}")
        sys.stdout.flush()
    artifact_uri = model_artifact_uri(bucket=bucket, base_key=base_key)
    print(f"Uploaded local model artifact to: {artifact_uri}")
    return artifact_uri
