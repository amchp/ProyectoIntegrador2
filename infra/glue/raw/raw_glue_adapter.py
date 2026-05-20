import sys
from pathlib import Path

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

try:
    from financial_sentiment_raw_core import run_raw_dataset_ingestion
except ModuleNotFoundError:
    COMMON_DIR = Path(__file__).resolve().parents[1] / "common"
    if str(COMMON_DIR) not in sys.path:
        sys.path.insert(0, str(COMMON_DIR))
    from financial_sentiment_raw_core import run_raw_dataset_ingestion


args = getResolvedOptions(
    sys.argv,
    [
        "JOB_NAME",
        "dataset_key",
        "raw_bucket",
        "raw_prefix",
    ],
)

sc = SparkContext()
glue_context = GlueContext(sc)
spark = glue_context.spark_session

job = Job(glue_context)
job.init(args["JOB_NAME"], args)

run_raw_dataset_ingestion(
    spark=spark,
    dataset_key=args["dataset_key"],
    raw_bucket=args["raw_bucket"],
    raw_prefix=args["raw_prefix"],
)

job.commit()
