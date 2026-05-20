from __future__ import annotations

from io import StringIO
from urllib.parse import quote

import boto3
import pandas as pd
from awsglue.context import GlueContext
from pandas_compat import ensure_pandas_spark_compat
from pyspark.sql import SparkSession


def load_curated_from_postgres(
    glue_context: GlueContext,
    spark: SparkSession,
    *,
    connection_name: str,
    source_table: str,
) -> pd.DataFrame:
    jdbc_conf = glue_context.extract_jdbc_conf(connection_name)
    url = jdbc_conf["fullUrl"]
    user = jdbc_conf["user"]
    password = jdbc_conf["password"]

    print(f"Reading curated dataset from PostgreSQL table: {source_table}")
    df = (
        spark.read
        .format("jdbc")
        .option("url", url)
        .option("dbtable", source_table)
        .option("user", user)
        .option("password", password)
        .option("driver", "org.postgresql.Driver")
        .load()
    )
    return df.toPandas()


def build_feature_exports(df: pd.DataFrame, *, snapshot_date: str) -> dict[str, pd.DataFrame]:
    working = df.copy()
    working["text"] = working["text"].fillna("").astype(str).str.strip()
    working["label_normalized"] = working["label_normalized"].fillna("missing").astype(str)
    working["split"] = working["split"].fillna("unspecified").astype(str)
    working["char_count"] = working["text"].str.len()
    working["word_count"] = working["text"].str.split().str.len()
    working["text_lower"] = working["text"].str.lower()
    working["duplicate_within_dataset"] = working.duplicated(subset=["dataset_label", "text_lower"])
    working["duplicate_global"] = working.duplicated(subset=["text_lower"])
    working["snapshot_date"] = snapshot_date

    dataset_summary = (
        working.groupby("dataset_label", dropna=False)
        .agg(
            rows=("text", "size"),
            splits=("split", lambda values: ", ".join(sorted(set(values)))),
            normalized_label_count=("label_normalized", lambda values: values.nunique(dropna=True)),
            avg_chars=("char_count", "mean"),
            median_words=("word_count", "median"),
            missing_text_pct=("text", lambda values: round((values.str.len() == 0).mean() * 100, 2)),
            duplicate_pct=("duplicate_within_dataset", lambda values: round(values.mean() * 100, 2)),
        )
        .reset_index()
    )
    dataset_summary["snapshot_date"] = snapshot_date

    label_distribution = pd.crosstab(
        working["dataset_label"],
        working["label_normalized"],
        normalize="index",
    ).round(3).reset_index()
    label_distribution["snapshot_date"] = snapshot_date

    split_distribution = pd.crosstab(working["dataset_label"], working["split"]).reset_index()
    split_distribution["snapshot_date"] = snapshot_date

    model_features = working[
        [
            "dataset_id",
            "dataset_label",
            "source_platform",
            "split",
            "text",
            "label_normalized",
            "label_id",
            "char_count",
            "word_count",
            "duplicate_within_dataset",
            "duplicate_global",
            "snapshot_date",
        ]
    ].copy()

    return {
        "model_features": model_features,
        "dataset_summary": dataset_summary,
        "label_distribution": label_distribution,
        "split_distribution": split_distribution,
    }


def delete_s3_prefix(s3_client, *, bucket: str, prefix: str) -> None:
    paginator = s3_client.get_paginator("list_objects_v2")
    keys: list[dict[str, str]] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            keys.append({"Key": item["Key"]})
            if len(keys) == 1000:
                s3_client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
                keys = []

    if keys:
        s3_client.delete_objects(Bucket=bucket, Delete={"Objects": keys})


def write_feature_exports(
    spark: SparkSession,
    exports: dict[str, pd.DataFrame],
    *,
    features_bucket: str,
    features_prefix: str,
) -> None:
    base_prefix = features_prefix.strip("/")
    ensure_pandas_spark_compat()
    s3_client = boto3.client("s3")
    snapshot_date = str(exports["model_features"]["snapshot_date"].iloc[0])
    partition_segment = f"snapshot_date={quote(snapshot_date, safe='')}"
    model_features_prefix = f"{base_prefix}/model_features/{partition_segment}/"
    reports_prefix = f"{base_prefix}/reports/{partition_segment}/"

    print(f"Deleting current daily model features partition: s3://{features_bucket}/{model_features_prefix}")
    delete_s3_prefix(s3_client, bucket=features_bucket, prefix=model_features_prefix)
    print(f"Deleting current daily reports partition: s3://{features_bucket}/{reports_prefix}")
    delete_s3_prefix(s3_client, bucket=features_bucket, prefix=reports_prefix)

    model_features_df = spark.createDataFrame(exports["model_features"])
    model_features_path = f"s3://{features_bucket}/{model_features_prefix}"
    (
        model_features_df.write
        .mode("overwrite")
        .format("parquet")
        .save(model_features_path)
    )
    print(f"Wrote model features to: {model_features_path}")

    for export_name in ["dataset_summary", "label_distribution", "split_distribution"]:
        buffer = StringIO()
        exports[export_name].to_csv(buffer, index=False)
        target_key = f"{reports_prefix}{export_name}.csv"
        s3_client.put_object(
            Bucket=features_bucket,
            Key=target_key,
            Body=buffer.getvalue().encode("utf-8"),
            ContentType="text/csv",
        )
        print(f"Wrote report to s3://{features_bucket}/{target_key}")
