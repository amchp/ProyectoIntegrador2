from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from finbert.artifacts import parse_s3_uri

LABEL_TO_ID = {"negative": 0, "neutral": 1, "positive": 2}


REQUIRED_COLUMNS = {"text", "label_normalized", "split"}


def _normalize_training_frame(df: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Training data is missing required columns: {sorted(missing)}")

    result = df.copy()
    result["text"] = result["text"].fillna("").astype(str).str.strip()
    result = result[result["text"] != ""].copy()
    result["label_normalized"] = result["label_normalized"].astype(str).str.lower()
    result = result[result["label_normalized"].isin(LABEL_TO_ID)].copy()
    if "label_id" not in result.columns:
        result["label_id"] = result["label_normalized"].map(LABEL_TO_ID)
    result["label_id"] = result["label_id"].astype(np.int64)
    result["split"] = result["split"].astype(str).str.lower()
    result = result[result["split"].isin(["train", "validation", "test"])].copy()

    split_counts = result["split"].value_counts().to_dict()
    for split_name in ["train", "validation", "test"]:
        if split_counts.get(split_name, 0) == 0:
            raise ValueError(f"Training data has no rows for split: {split_name}")

    return result.reset_index(drop=True)


def load_training_data(csv_path: str | Path | None = None, s3_uri: str | None = None) -> pd.DataFrame:
    if bool(csv_path) == bool(s3_uri):
        raise ValueError("Provide exactly one of csv_path or s3_uri.")
    if csv_path:
        return _normalize_training_frame(pd.read_csv(csv_path))
    return _normalize_training_frame(load_feature_snapshot_from_s3(str(s3_uri)))


def load_feature_snapshot_from_s3(s3_uri: str) -> pd.DataFrame:
    import boto3

    source = parse_s3_uri(s3_uri)
    s3_client = boto3.client("s3")
    paginator = s3_client.get_paginator("list_objects_v2")
    parquet_keys: list[str] = []

    for page in paginator.paginate(Bucket=source.bucket, Prefix=f"{source.key}/"):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith(".parquet"):
                parquet_keys.append(key)

    if not parquet_keys:
        raise FileNotFoundError(f"No parquet files found under {s3_uri}")

    frames = []
    with TemporaryDirectory(prefix="finbert-features-") as tmp:
        tmp_dir = Path(tmp)
        for index, key in enumerate(sorted(parquet_keys)):
            local_path = tmp_dir / f"part-{index}.parquet"
            s3_client.download_file(source.bucket, key, str(local_path))
            frames.append(pd.read_parquet(local_path))

    return pd.concat(frames, ignore_index=True)
