#!/usr/bin/env python3
"""Create or update the AWS Glue jobs for the financial sentiment pipeline."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from botocore.exceptions import ClientError
from utils.aws import aws_clients, lab_role_arn
from utils.common import resolve_region
from utils.glue import base_default_arguments, create_or_update_job, job_definition, s3_uri


AWS_REGION = "us-east-1"
DEPLOY_BUCKET = "proyecto-integrador-2"
DEPLOY_PREFIX = "deploy/glue"
GLUE_VERSION = "4.0"
WORKER_TYPE = "G.1X"
NUMBER_OF_WORKERS = 2
TIMEOUT_MINUTES = 60
MAX_RETRIES = 0
DEFAULT_MAX_CONCURRENT_RUNS = 1
RAW_MAX_CONCURRENT_RUNS = 1
RAW_JOB_NAME = "glue_financial_sentiment_raw"
CURATED_JOB_NAME = "glue_financial_sentiment_curated"
FEATURES_JOB_NAME = "glue_financial_sentiment_features"
RAW_CONNECTIONS: list[str] = []
CURATED_CONNECTIONS = ["Proyecto Financial Sentiment RDS connection"]
FEATURES_CONNECTIONS = ["Proyecto Financial Sentiment RDS connection"]
INFRA_DIR = Path(__file__).resolve().parent
GLUE_DIR = INFRA_DIR / "glue"
COMMON_DIR = GLUE_DIR / "common"
RAW_SCRIPT_PATH = GLUE_DIR / "raw" / "raw_glue_adapter.py"
CURATED_SCRIPT_PATH = GLUE_DIR / "curated" / "curated_glue_adapter.py"
FEATURES_SCRIPT_PATH = GLUE_DIR / "features" / "features_glue_adapter.py"
RAW_REQUIREMENTS_PATH = GLUE_DIR / "requirements_raw.txt"
CURATED_REQUIREMENTS_PATH = GLUE_DIR / "requirements_curated.txt"
FEATURES_REQUIREMENTS_PATH = GLUE_DIR / "requirements_features.txt"


def load_requirements(path: Path) -> str:
    requirements = []
    for line in path.read_text(encoding="utf-8").splitlines():
        package = line.strip()
        if package and not package.startswith("#"):
            requirements.append(package)
    return ",".join(requirements)


def upload_file(s3_client, *, bucket: str, key: str, local_path: Path) -> str:
    s3_client.upload_file(str(local_path), bucket, key)
    uri = s3_uri(bucket, key)
    print(f"Uploaded {local_path.name} to {uri}")
    return uri


def build_common_zip(common_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(common_dir.glob("*.py")):
            archive.write(file_path, arcname=file_path.name)
    return buffer.getvalue()


def upload_common_zip(s3_client, *, bucket: str, key: str, common_dir: Path) -> str:
    payload = build_common_zip(common_dir)
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=payload,
        ContentType="application/zip",
    )
    uri = s3_uri(bucket, key)
    print(f"Uploaded common library bundle to {uri}")
    return uri


def main() -> None:
    region = resolve_region(AWS_REGION)
    s3_client, glue_client, iam_client = aws_clients(region, "s3", "glue", "iam")
    role_arn = lab_role_arn(iam_client)

    common_zip_uri = upload_common_zip(
        s3_client,
        bucket=DEPLOY_BUCKET,
        key=f"{DEPLOY_PREFIX}/common/financial_sentiment_common.zip",
        common_dir=COMMON_DIR,
    )
    raw_script_uri = upload_file(
        s3_client,
        bucket=DEPLOY_BUCKET,
        key=f"{DEPLOY_PREFIX}/raw/raw_glue_adapter.py",
        local_path=RAW_SCRIPT_PATH,
    )
    curated_script_uri = upload_file(
        s3_client,
        bucket=DEPLOY_BUCKET,
        key=f"{DEPLOY_PREFIX}/curated/curated_glue_adapter.py",
        local_path=CURATED_SCRIPT_PATH,
    )
    features_script_uri = upload_file(
        s3_client,
        bucket=DEPLOY_BUCKET,
        key=f"{DEPLOY_PREFIX}/features/features_glue_adapter.py",
        local_path=FEATURES_SCRIPT_PATH,
    )

    create_or_update_job(
        glue_client,
        job_name=RAW_JOB_NAME,
        definition=job_definition(
            role_arn=role_arn,
            script_location=raw_script_uri,
            default_arguments=base_default_arguments(
                deploy_bucket=DEPLOY_BUCKET,
                deploy_prefix=DEPLOY_PREFIX,
                additional_python_modules=load_requirements(RAW_REQUIREMENTS_PATH),
                extra_py_files_uri=common_zip_uri,
            ),
            connections=RAW_CONNECTIONS,
            glue_version=GLUE_VERSION,
            worker_type=WORKER_TYPE,
            number_of_workers=NUMBER_OF_WORKERS,
            timeout_minutes=TIMEOUT_MINUTES,
            max_retries=MAX_RETRIES,
            max_concurrent_runs=RAW_MAX_CONCURRENT_RUNS,
        ),
    )
    create_or_update_job(
        glue_client,
        job_name=CURATED_JOB_NAME,
        definition=job_definition(
            role_arn=role_arn,
            script_location=curated_script_uri,
            default_arguments=base_default_arguments(
                deploy_bucket=DEPLOY_BUCKET,
                deploy_prefix=DEPLOY_PREFIX,
                additional_python_modules=load_requirements(CURATED_REQUIREMENTS_PATH),
                extra_py_files_uri=common_zip_uri,
            ),
            connections=CURATED_CONNECTIONS,
            glue_version=GLUE_VERSION,
            worker_type=WORKER_TYPE,
            number_of_workers=NUMBER_OF_WORKERS,
            timeout_minutes=TIMEOUT_MINUTES,
            max_retries=MAX_RETRIES,
            max_concurrent_runs=DEFAULT_MAX_CONCURRENT_RUNS,
        ),
    )
    create_or_update_job(
        glue_client,
        job_name=FEATURES_JOB_NAME,
        definition=job_definition(
            role_arn=role_arn,
            script_location=features_script_uri,
            default_arguments=base_default_arguments(
                deploy_bucket=DEPLOY_BUCKET,
                deploy_prefix=DEPLOY_PREFIX,
                additional_python_modules=load_requirements(FEATURES_REQUIREMENTS_PATH),
                extra_py_files_uri=common_zip_uri,
            ),
            connections=FEATURES_CONNECTIONS,
            glue_version=GLUE_VERSION,
            worker_type=WORKER_TYPE,
            number_of_workers=NUMBER_OF_WORKERS,
            timeout_minutes=TIMEOUT_MINUTES,
            max_retries=MAX_RETRIES,
            max_concurrent_runs=DEFAULT_MAX_CONCURRENT_RUNS,
        ),
    )


if __name__ == "__main__":
    try:
        main()
    except ClientError as error:
        raise SystemExit(f"AWS error: {error}")
