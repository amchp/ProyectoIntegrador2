from __future__ import annotations

from awsglue.context import GlueContext
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F


LABEL_TO_ID = {"negative": 0, "neutral": 1, "positive": 2}


def load_all_raw_datasets(
    spark: SparkSession,
    *,
    raw_bucket: str,
    raw_prefix: str,
) -> DataFrame:
    base_prefix = raw_prefix.strip("/")
    path = f"s3://{raw_bucket}/{base_prefix}/*/canonical/"
    print(f"Reading canonical raw datasets from: {path}")
    return spark.read.parquet(path)


def build_curated_dataframe(raw_df: DataFrame) -> DataFrame:
    trimmed = (
        raw_df
        .withColumn("text", F.trim(F.col("text")))
        .filter(F.col("text").isNotNull())
        .filter(F.col("text") != "")
        .filter(F.col("label_normalized").isin(["negative", "neutral", "positive"]))
        .withColumn(
            "label_id",
            F.when(F.col("label_normalized") == F.lit("negative"), F.lit(LABEL_TO_ID["negative"]))
            .when(F.col("label_normalized") == F.lit("neutral"), F.lit(LABEL_TO_ID["neutral"]))
            .when(F.col("label_normalized") == F.lit("positive"), F.lit(LABEL_TO_ID["positive"]))
            .cast("int")
        )
    )

    return trimmed.select(
        "dataset_id",
        "dataset_label",
        "source_platform",
        "split",
        "text",
        "label_normalized",
        "label_id",
    )


def write_curated_to_postgres(
    glue_context: GlueContext,
    curated_df: DataFrame,
    *,
    connection_name: str,
    target_table: str,
) -> None:
    jdbc_conf = glue_context.extract_jdbc_conf(connection_name)
    url = jdbc_conf["fullUrl"]
    user = jdbc_conf["user"]
    password = jdbc_conf["password"]

    print(f"Writing curated dataset to PostgreSQL table: {target_table}")
    (
        curated_df.write
        .format("jdbc")
        .option("url", url)
        .option("dbtable", target_table)
        .option("user", user)
        .option("password", password)
        .option("driver", "org.postgresql.Driver")
        .mode("overwrite")
        .save()
    )
