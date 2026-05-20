from __future__ import annotations

import json
import re
import unicodedata
from html import unescape
from io import StringIO
from pathlib import Path

import boto3
import kagglehub
import pandas as pd
from datasets import load_dataset
from pandas_compat import ensure_pandas_spark_compat
from pyspark.sql import SparkSession


BASE_COLUMNS = [
    "dataset_id",
    "dataset_label",
    "source_platform",
    "split",
    "text",
    "label_normalized",
]

POSITIVE_LABELS = {
    "positive",
    "mildly positive",
    "moderately positive",
    "strong positive",
}
NEGATIVE_LABELS = {
    "negative",
    "mildly negative",
    "moderately negative",
    "strong negative",
}
NEUTRAL_LABELS = {"neutral"}


def clean_text(value: object) -> str:
    text = unescape(str(value))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8", "ignore")
    text = re.sub(r"[^a-zA-Z\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def normalize_label(value: object) -> str | None:
    if pd.isna(value):
        return None
    normalized = str(value).strip().lower()
    if normalized in POSITIVE_LABELS:
        return "positive"
    if normalized in NEGATIVE_LABELS:
        return "negative"
    if normalized in NEUTRAL_LABELS:
        return "neutral"
    return normalized


def curate_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df[BASE_COLUMNS].copy()


def build_split_payload(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {"dataframe": df}


def compute_split_counts(total_rows: int) -> dict[str, int]:
    test_rows = round(total_rows * 0.2)
    validation_rows = round(total_rows * 0.2)
    train_rows = total_rows - test_rows - validation_rows

    if train_rows < 0:
        validation_rows = max(validation_rows + train_rows, 0)
        train_rows = total_rows - test_rows - validation_rows
    if train_rows < 0:
        test_rows = max(test_rows + train_rows, 0)
        train_rows = total_rows - test_rows - validation_rows

    return {
        "train": train_rows,
        "validation": validation_rows,
        "test": test_rows,
    }


def derive_curated_splits(source_payload: dict[str, dict[str, pd.DataFrame]]) -> dict[str, dict[str, pd.DataFrame | str]]:
    preferred_order = ["train", "validation", "test"]
    ordered_split_names = [split_name for split_name in preferred_order if split_name in source_payload["splits"]]
    ordered_split_names.extend(
        split_name
        for split_name in source_payload["splits"].keys()
        if split_name not in ordered_split_names
    )

    frames = [source_payload["splits"][split_name]["dataframe"] for split_name in ordered_split_names]
    raw_df = pd.concat(frames, ignore_index=True, sort=False)
    derived_from = ",".join(ordered_split_names)
    counts = compute_split_counts(len(raw_df))

    train_end = counts["train"]
    validation_end = train_end + counts["validation"]

    return {
        "train": {
            "dataframe": raw_df.iloc[:train_end].reset_index(drop=True),
            "derived_from": derived_from,
        },
        "validation": {
            "dataframe": raw_df.iloc[train_end:validation_end].reset_index(drop=True),
            "derived_from": derived_from,
        },
        "test": {
            "dataframe": raw_df.iloc[validation_end:].reset_index(drop=True),
            "derived_from": derived_from,
        },
    }


def standardize_lwrf(df: pd.DataFrame, split_name: str) -> pd.DataFrame:
    standardized = df.copy()
    standardized["dataset_id"] = "lwrf42/financial-sentiment-dataset"
    standardized["dataset_label"] = "lwrf42_financial_sentiment_dataset"
    standardized["source_platform"] = "huggingface"
    standardized["split"] = split_name
    standardized["text"] = standardized["input"].map(clean_text)
    standardized["label_raw"] = standardized["output"].astype(str).str.strip().str.lower()
    standardized["label_normalized"] = standardized["label_raw"].map(normalize_label)
    return standardized


def standardize_kenpache_english(df: pd.DataFrame, split_name: str) -> pd.DataFrame:
    standardized = df[df["language"] == "en"].copy()
    standardized["dataset_id"] = "Kenpache/multilingual-financial-sentiment"
    standardized["dataset_label"] = "kenpache_multilingual_financial_sentiment_en"
    standardized["source_platform"] = "huggingface"
    standardized["split"] = split_name
    standardized["text"] = standardized["sentence"].map(clean_text)
    standardized["label_raw"] = standardized["label"].astype(str).str.strip().str.lower()
    standardized["label_normalized"] = standardized["label_raw"].map(normalize_label)
    return standardized


def standardize_maguid(df: pd.DataFrame, split_name: str) -> pd.DataFrame:
    standardized = df.copy()
    standardized["dataset_id"] = "maguid28/combined_financial_phrasebank_twitter_news_sentiment"
    standardized["dataset_label"] = "maguid28_combined_financial_phrasebank_twitter_news_sentiment"
    standardized["source_platform"] = "huggingface"
    standardized["split"] = split_name
    standardized["text"] = standardized["text"].map(clean_text)
    standardized["label_raw"] = standardized["polarity"].astype(str).str.strip().str.lower()
    standardized["label_normalized"] = standardized["label_raw"].map(normalize_label)
    return standardized


def standardize_kaggle(df: pd.DataFrame, split_name: str) -> pd.DataFrame:
    standardized = df.copy()
    standardized["dataset_id"] = "sbhatti/financial-sentiment-analysis"
    standardized["dataset_label"] = "sbhatti_financial_sentiment_analysis"
    standardized["source_platform"] = "kaggle"
    standardized["split"] = split_name
    standardized["text"] = standardized["Sentence"].map(clean_text)
    standardized["label_raw"] = standardized["Sentiment"].astype(str).str.strip().str.lower()
    standardized["label_normalized"] = standardized["label_raw"].map(normalize_label)
    return standardized


def summarize_frame(df: pd.DataFrame) -> dict[str, int]:
    return {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "unique_raw_labels": int(df["label_raw"].nunique(dropna=True)),
        "unique_normalized_labels": int(df["label_normalized"].nunique(dropna=True)),
    }


def load_huggingface_source(config: dict[str, object]) -> dict[str, dict[str, dict[str, pd.DataFrame]]]:
    dataset = load_dataset(config["repo_id"], verification_mode="no_checks")
    splits: dict[str, dict[str, pd.DataFrame]] = {}
    for split_name, split_dataset in dataset.items():
        raw_df = split_dataset.to_pandas()
        splits[split_name] = build_split_payload(raw_df)
    return {"splits": splits}


def load_kaggle_source(config: dict[str, object]) -> dict[str, dict[str, dict[str, pd.DataFrame]]]:
    kaggle_download_root = kagglehub.dataset_download(config["repo_id"])
    download_root = Path(kaggle_download_root)
    file_candidates = sorted(download_root.rglob("*.csv"))
    if not file_candidates:
        raise FileNotFoundError("No CSV files were found inside the Kaggle download.")

    raw_frames = [pd.read_csv(csv_path) for csv_path in file_candidates]
    raw_df = pd.concat(raw_frames, ignore_index=True, sort=False)
    split_name = str(config.get("default_split", "train"))
    return {"splits": {split_name: build_split_payload(raw_df)}}


SOURCE_CONFIGS: dict[str, dict[str, object]] = {
    "lwrf42_financial_sentiment_dataset": {
        "key": "lwrf42_financial_sentiment_dataset",
        "repo_id": "lwrf42/financial-sentiment-dataset",
        "source_platform": "huggingface",
        "loader": load_huggingface_source,
        "standardizer": standardize_lwrf,
    },
    "kenpache_multilingual_financial_sentiment": {
        "key": "kenpache_multilingual_financial_sentiment",
        "repo_id": "Kenpache/multilingual-financial-sentiment",
        "source_platform": "huggingface",
        "loader": load_huggingface_source,
        "standardizer": standardize_kenpache_english,
    },
    "maguid28_combined_financial_phrasebank_twitter_news_sentiment": {
        "key": "maguid28_combined_financial_phrasebank_twitter_news_sentiment",
        "repo_id": "maguid28/combined_financial_phrasebank_twitter_news_sentiment",
        "source_platform": "huggingface",
        "loader": load_huggingface_source,
        "standardizer": standardize_maguid,
    },
    "sbhatti_financial_sentiment_analysis": {
        "key": "sbhatti_financial_sentiment_analysis",
        "repo_id": "sbhatti/financial-sentiment-analysis",
        "source_platform": "kaggle",
        "loader": load_kaggle_source,
        "standardizer": standardize_kaggle,
        "default_split": "train",
    },
}


def _write_manifest(bucket: str, key: str, manifest: list[dict[str, object]]) -> None:
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(manifest, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def _write_split_preview(bucket: str, key: str, df: pd.DataFrame) -> None:
    buffer = StringIO()
    df.to_csv(buffer, index=False)
    boto3.client("s3").put_object(
        Bucket=bucket,
        Key=key,
        Body=buffer.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )


def run_raw_dataset_ingestion(
    *,
    spark: SparkSession,
    dataset_key: str,
    raw_bucket: str,
    raw_prefix: str,
) -> None:
    if dataset_key not in SOURCE_CONFIGS:
        raise ValueError(f"Unsupported dataset_key: {dataset_key}")

    config = SOURCE_CONFIGS[dataset_key]
    loader = config["loader"]
    standardizer = config["standardizer"]

    source_payload = loader(config)  # type: ignore[misc]
    curated_splits = derive_curated_splits(source_payload)

    manifest: list[dict[str, object]] = []
    dataset_frames: list[pd.DataFrame] = []
    base_prefix = raw_prefix.strip("/")

    for split_name, split_payload in curated_splits.items():
        standardized = standardizer(split_payload["dataframe"], split_name)  # type: ignore[misc]
        standardized = standardized[standardized["text"].notna()].copy()
        standardized["text"] = standardized["text"].astype(str).str.strip()
        standardized = standardized[standardized["text"] != ""].copy()

        summary = summarize_frame(standardized)
        dataset_frames.append(standardized)
        manifest.append(
            {
                "dataset_key": dataset_key,
                "dataset_id": config["repo_id"],
                "source_platform": config["source_platform"],
                "status": "downloaded",
                "split": split_name,
                "rows": summary["rows"],
                "columns": summary["columns"],
                "unique_raw_labels": summary["unique_raw_labels"],
                "unique_normalized_labels": summary["unique_normalized_labels"],
                "derived_from": split_payload.get("derived_from"),
            }
        )
        _write_split_preview(
            raw_bucket,
            f"{base_prefix}/{dataset_key}/split_previews/{split_name}.csv",
            curate_columns(standardized),
        )

    dataset_frame = pd.concat(dataset_frames, ignore_index=True, sort=False)
    exported = curate_columns(dataset_frame)
    ensure_pandas_spark_compat()
    spark_df = spark.createDataFrame(exported)
    target_path = f"s3://{raw_bucket}/{base_prefix}/{dataset_key}/canonical/"
    (
        spark_df.write
        .mode("overwrite")
        .format("parquet")
        .save(target_path)
    )

    manifest.append(
        {
            "dataset_key": dataset_key,
            "dataset_id": config["repo_id"],
            "source_platform": config["source_platform"],
            "status": "exported",
            "split": "all",
            "rows": int(len(exported)),
            "columns": int(len(exported.columns)),
            "canonical_path": target_path,
        }
    )
    _write_manifest(
        raw_bucket,
        f"{base_prefix}/{dataset_key}/manifest/download_manifest.json",
        manifest,
    )
    print(f"Wrote canonical dataset for {dataset_key} to {target_path}")
