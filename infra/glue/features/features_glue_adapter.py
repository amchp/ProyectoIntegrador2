import sys
from pathlib import Path

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

try:
    from financial_sentiment_features_core import (
        build_feature_exports,
        load_curated_from_postgres,
        write_feature_exports,
    )
except ModuleNotFoundError:
    COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
    if str(COMMON_DIR) not in sys.path:
        sys.path.insert(0, str(COMMON_DIR))
    from financial_sentiment_features_core import (
        build_feature_exports,
        load_curated_from_postgres,
        write_feature_exports,
    )


args = getResolvedOptions(
    sys.argv,
    [
        "JOB_NAME",
        "connection_name",
        "source_table",
        "features_bucket",
        "features_prefix",
        "snapshot_date",
    ],
)

sc = SparkContext()
glue_context = GlueContext(sc)
spark = glue_context.spark_session

job = Job(glue_context)
job.init(args["JOB_NAME"], args)

curated_df = load_curated_from_postgres(
    glue_context,
    spark,
    connection_name=args["connection_name"],
    source_table=args["source_table"],
)
exports = build_feature_exports(
    curated_df,
    snapshot_date=args["snapshot_date"],
)
write_feature_exports(
    spark,
    exports,
    features_bucket=args["features_bucket"],
    features_prefix=args["features_prefix"],
)

job.commit()
