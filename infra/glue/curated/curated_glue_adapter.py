import sys
from pathlib import Path

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

try:
    from financial_sentiment_curated_core import (
        build_curated_dataframe,
        load_all_raw_datasets,
        write_curated_to_postgres,
    )
except ModuleNotFoundError:
    COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
    if str(COMMON_DIR) not in sys.path:
        sys.path.insert(0, str(COMMON_DIR))
    from financial_sentiment_curated_core import (
        build_curated_dataframe,
        load_all_raw_datasets,
        write_curated_to_postgres,
    )


args = getResolvedOptions(
    sys.argv,
    [
        "JOB_NAME",
        "connection_name",
        "raw_bucket",
        "raw_prefix",
        "target_table",
    ],
)

sc = SparkContext()
glue_context = GlueContext(sc)
spark = glue_context.spark_session

job = Job(glue_context)
job.init(args["JOB_NAME"], args)

raw_df = load_all_raw_datasets(
    spark,
    raw_bucket=args["raw_bucket"],
    raw_prefix=args["raw_prefix"],
)
curated_df = build_curated_dataframe(raw_df)
write_curated_to_postgres(
    glue_context,
    curated_df,
    connection_name=args["connection_name"],
    target_table=args["target_table"],
)

job.commit()
