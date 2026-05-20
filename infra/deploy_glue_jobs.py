#!/usr/bin/env python3
"""Create or update the AWS Glue jobs for the financial sentiment pipeline."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from utils.common import require_env, resolve_region


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


def s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


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


def glue_job_exists(glue_client, job_name: str) -> bool:
    try:
        glue_client.get_job(JobName=job_name)
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "EntityNotFoundException":
            return False
        raise


def base_default_arguments(
    *,
    deploy_bucket: str,
    deploy_prefix: str,
    additional_python_modules: str,
    extra_py_files_uri: str,
) -> dict[str, str]:
    arguments = {
        "--job-language": "python",
        "--enable-metrics": "true",
        "--enable-continuous-cloudwatch-log": "true",
        "--TempDir": s3_uri(deploy_bucket, f"{deploy_prefix}/temp/"),
        "--extra-py-files": extra_py_files_uri,
    }
    if additional_python_modules:
        arguments["--additional-python-modules"] = additional_python_modules
    return arguments


def job_definition(
    *,
    role_arn: str,
    script_location: str,
    default_arguments: dict[str, str],
    connections: list[str],
    max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS,
) -> dict:
    definition = {
        "Role": role_arn,
        "ExecutionProperty": {"MaxConcurrentRuns": max_concurrent_runs},
        "Command": {
            "Name": "glueetl",
            "ScriptLocation": script_location,
            "PythonVersion": "3",
        },
        "DefaultArguments": default_arguments,
        "GlueVersion": GLUE_VERSION,
        "WorkerType": WORKER_TYPE,
        "NumberOfWorkers": NUMBER_OF_WORKERS,
        "Timeout": TIMEOUT_MINUTES,
        "MaxRetries": MAX_RETRIES,
    }
    if connections:
        definition["Connections"] = {"Connections": connections}
    return definition


def create_or_update_job(
    glue_client,
    *,
    job_name: str,
    definition: dict,
) -> None:
    if glue_job_exists(glue_client, job_name):
        glue_client.update_job(JobName=job_name, JobUpdate=definition)
        print(f"Updated Glue job: {job_name}")
        return

    glue_client.create_job(Name=job_name, **definition)
    print(f"Created Glue job: {job_name}")


def deploy_glue_jobs() -> None:
    region = resolve_region(AWS_REGION)
    glue_service_role_arn = require_env(
        "GLUE_SERVICE_ROLE_ARN",
        placeholder_prefixes=("arn:aws:iam::123456789012:",),
    )
    s3_client = boto3.client("s3", region_name=region)
    glue_client = boto3.client("glue", region_name=region)

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
            role_arn=glue_service_role_arn,
            script_location=raw_script_uri,
            default_arguments=base_default_arguments(
                deploy_bucket=DEPLOY_BUCKET,
                deploy_prefix=DEPLOY_PREFIX,
                additional_python_modules=load_requirements(RAW_REQUIREMENTS_PATH),
                extra_py_files_uri=common_zip_uri,
            ),
            connections=RAW_CONNECTIONS,
            max_concurrent_runs=RAW_MAX_CONCURRENT_RUNS,
        ),
    )
    create_or_update_job(
        glue_client,
        job_name=CURATED_JOB_NAME,
        definition=job_definition(
            role_arn=glue_service_role_arn,
            script_location=curated_script_uri,
            default_arguments=base_default_arguments(
                deploy_bucket=DEPLOY_BUCKET,
                deploy_prefix=DEPLOY_PREFIX,
                additional_python_modules=load_requirements(CURATED_REQUIREMENTS_PATH),
                extra_py_files_uri=common_zip_uri,
            ),
            connections=CURATED_CONNECTIONS,
        ),
    )
    create_or_update_job(
        glue_client,
        job_name=FEATURES_JOB_NAME,
        definition=job_definition(
            role_arn=glue_service_role_arn,
            script_location=features_script_uri,
            default_arguments=base_default_arguments(
                deploy_bucket=DEPLOY_BUCKET,
                deploy_prefix=DEPLOY_PREFIX,
                additional_python_modules=load_requirements(FEATURES_REQUIREMENTS_PATH),
                extra_py_files_uri=common_zip_uri,
            ),
            connections=FEATURES_CONNECTIONS,
        ),
    )


if __name__ == "__main__":
    try:
        deploy_glue_jobs()
    except ClientError as error:
        raise SystemExit(f"AWS error: {error}")
