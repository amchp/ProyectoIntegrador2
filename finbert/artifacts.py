from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


DEPLOYABLE_MODEL_FILES = {
    "config.json",
    "model.safetensors",
    "pytorch_model.bin",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
    "merges.txt",
}


@dataclass(frozen=True)
class S3Uri:
    bucket: str
    key: str

    def __str__(self) -> str:
        return f"s3://{self.bucket}/{self.key}".rstrip("/")


def parse_s3_uri(uri: str) -> S3Uri:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Expected an S3 URI, got: {uri}")
    return S3Uri(bucket=parsed.netloc, key=parsed.path.lstrip("/").rstrip("/"))


def build_feature_snapshot_uri(
    *,
    bucket: str,
    features_prefix: str,
    snapshot_date: str,
) -> str:
    prefix = features_prefix.strip("/")
    return f"s3://{bucket}/{prefix}/model_features/snapshot_date={snapshot_date}/"


def build_model_artifact_uri(
    *,
    bucket: str,
    artifact_prefix: str,
    snapshot_date: str,
    run_id: str,
) -> str:
    prefix = artifact_prefix.strip("/")
    return f"s3://{bucket}/{prefix}/snapshot_date={snapshot_date}/run_id={run_id}/"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare_deployable_artifact(
    *,
    checkpoint_dir: Path,
    output_dir: Path,
    metrics: dict,
    metadata: dict,
) -> Path:
    if not checkpoint_dir.exists():
        raise FileNotFoundError(f"Checkpoint directory does not exist: {checkpoint_dir}")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    copied = []
    for source in checkpoint_dir.iterdir():
        if source.is_file() and source.name in DEPLOYABLE_MODEL_FILES:
            shutil.copy2(source, output_dir / source.name)
            copied.append(source.name)

    if "config.json" not in copied:
        raise FileNotFoundError(f"config.json was not found in {checkpoint_dir}")
    if not ({"model.safetensors", "pytorch_model.bin"} & set(copied)):
        raise FileNotFoundError(f"No model weights were found in {checkpoint_dir}")
    if "tokenizer.json" not in copied and "vocab.txt" not in copied:
        raise FileNotFoundError(f"No tokenizer files were found in {checkpoint_dir}")

    write_json(output_dir / "metrics.json", metrics)
    write_json(output_dir / "training_metadata.json", {**metadata, "artifact_files": sorted(copied)})
    return output_dir


def upload_directory_to_s3(local_dir: Path, artifact_uri: str) -> None:
    import boto3

    target = parse_s3_uri(artifact_uri)
    s3_client = boto3.client("s3")
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue
        relative_key = path.relative_to(local_dir).as_posix()
        key = f"{target.key}/{relative_key}" if target.key else relative_key
        s3_client.upload_file(str(path), target.bucket, key)


def download_s3_prefix(artifact_uri: str, local_dir: Path) -> Path:
    import boto3

    source = parse_s3_uri(artifact_uri)
    local_dir.mkdir(parents=True, exist_ok=True)
    s3_client = boto3.client("s3")
    paginator = s3_client.get_paginator("list_objects_v2")
    found = False

    for page in paginator.paginate(Bucket=source.bucket, Prefix=f"{source.key}/"):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith("/"):
                continue
            found = True
            source_prefix = f"{source.key}/"
            relative = key[len(source_prefix) :] if key.startswith(source_prefix) else key
            target = local_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            s3_client.download_file(source.bucket, key, str(target))

    if not found:
        raise FileNotFoundError(f"No objects found under {artifact_uri}")
    return local_dir
