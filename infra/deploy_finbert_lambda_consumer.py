#!/usr/bin/env python3
"""Deploy the Kinesis-triggered FinBERT inference Lambda."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from utils.aws import aws_clients, lab_role_arn
from utils.common import require_csv_env, require_env, resolve_region
from utils.ec2 import require_instance_by_name
from utils.lambda_functions import ensure_event_source_mapping, ensure_zip_function
from utils.security_groups import get_security_group_id

AWS_REGION = "us-east-1"
FUNCTION_NAME = "proyecto-finbert-inference-consumer"
STREAM_NAME = "proyecto-finbert-inference-requests"
LAMBDA_SECURITY_GROUP_NAME = "proyecto-finbert-lambda-sg"
EC2_INSTANCE_NAME = "proyecto-finbert-ec2"
RESULT_BUCKET = "proyecto-integrador-2-features-amce"
RESULT_PREFIX = "inference/finbert/results"
RUNTIME = "python3.13"
HANDLER = "finbert_inference_consumer.lambda_handler"
TIMEOUT_SECONDS = 30
MEMORY_MB = 256
TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "inference",
    "ManagedBy": "python-script",
}


def package_lambda() -> bytes:
    source = Path(__file__).resolve().parent / "lambda" / "finbert_inference_consumer.py"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(source, arcname="finbert_inference_consumer.py")
    return buffer.getvalue()


def finbert_private_ip(ec2_client) -> str:
    instance = require_instance_by_name(
        ec2_client,
        name=EC2_INSTANCE_NAME,
        states=["running"],
        message=f"No running EC2 instance found with Name={EC2_INSTANCE_NAME}.",
    )
    private_ip = instance.get("PrivateIpAddress", "")
    if not private_ip:
        raise RuntimeError(f"EC2 instance {instance['InstanceId']} does not have a private IP address.")
    return private_ip


def ensure_function(lambda_client, *, zip_bytes: bytes, role: str, subnet_ids: list[str], security_group_id: str, api_url: str) -> str:
    environment = {
        "Variables": {
            "FINBERT_API_URL": api_url,
            "FINBERT_RESULT_BUCKET": RESULT_BUCKET,
            "FINBERT_RESULT_PREFIX": RESULT_PREFIX,
        }
    }
    vpc_config = {
        "SubnetIds": subnet_ids,
        "SecurityGroupIds": [security_group_id],
    }
    return ensure_zip_function(
        lambda_client,
        function_name=FUNCTION_NAME,
        zip_bytes=zip_bytes,
        role=role,
        runtime=RUNTIME,
        handler=HANDLER,
        timeout_seconds=TIMEOUT_SECONDS,
        memory_mb=MEMORY_MB,
        environment=environment,
        vpc_config=vpc_config,
        tags=TAGS,
    )


if __name__ == "__main__":
    region = resolve_region(AWS_REGION)
    ec2_client, iam_client, kinesis_client, lambda_client = aws_clients(
        region,
        "ec2",
        "iam",
        "kinesis",
        "lambda",
    )

    vpc_id = require_env("VPC_ID", placeholder_prefixes=("vpc-xxxxxxxx",))
    private_subnet_ids = require_csv_env("PRIVATE_SUBNET_IDS", min_values=1, placeholder_prefixes=("subnet-xxxxxxxx",))
    lambda_group_id = get_security_group_id(
        ec2_client,
        group_name=LAMBDA_SECURITY_GROUP_NAME,
        vpc_id=vpc_id,
    )
    if not lambda_group_id:
        raise RuntimeError(f"Security group {LAMBDA_SECURITY_GROUP_NAME} does not exist. Run create_security_groups.py.")

    private_ip = finbert_private_ip(ec2_client)
    api_url = f"http://{private_ip}:8000/predict"
    stream = kinesis_client.describe_stream_summary(StreamName=STREAM_NAME)["StreamDescriptionSummary"]
    function_arn = ensure_function(
        lambda_client,
        zip_bytes=package_lambda(),
        role=lab_role_arn(iam_client),
        subnet_ids=private_subnet_ids,
        security_group_id=lambda_group_id,
        api_url=api_url,
    )
    ensure_event_source_mapping(
        lambda_client,
        stream_arn=stream["StreamARN"],
        function_name=FUNCTION_NAME,
    )

    print(f"Lambda function ready: {function_arn}")
    print(f"FinBERT API URL: {api_url}")
    print(f"Result prefix: s3://{RESULT_BUCKET}/{RESULT_PREFIX}/")
