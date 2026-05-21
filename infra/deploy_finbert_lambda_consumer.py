#!/usr/bin/env python3
"""Deploy the Kinesis-triggered FinBERT inference Lambda."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from utils.common import require_csv_env, require_env, resolve_region
from utils.security_groups import get_security_group_id

AWS_REGION = "us-east-1"
FUNCTION_NAME = "proyecto-finbert-inference-consumer"
STREAM_NAME = "proyecto-finbert-inference-requests"
LAMBDA_ROLE_NAME = "LabRole"
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


def role_arn(iam_client) -> str:
    return iam_client.get_role(RoleName=LAMBDA_ROLE_NAME)["Role"]["Arn"]


def find_finbert_private_ip(ec2_client) -> str:
    reservations = ec2_client.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [EC2_INSTANCE_NAME]},
            {"Name": "instance-state-name", "Values": ["running"]},
        ]
    )["Reservations"]
    instances = [instance for reservation in reservations for instance in reservation["Instances"]]
    if not instances:
        raise RuntimeError(f"No running EC2 instance found with Name={EC2_INSTANCE_NAME}.")
    private_ip = instances[0].get("PrivateIpAddress", "")
    if not private_ip:
        raise RuntimeError(f"EC2 instance {instances[0]['InstanceId']} does not have a private IP address.")
    return private_ip


def wait_for_function_ready(lambda_client) -> None:
    while True:
        config = lambda_client.get_function_configuration(FunctionName=FUNCTION_NAME)
        state = config.get("State")
        last_update = config.get("LastUpdateStatus")
        if state == "Active" and last_update in {None, "Successful"}:
            return
        print(f"Waiting for Lambda function readiness: State={state} LastUpdateStatus={last_update}")
        time.sleep(5)


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
    try:
        response = lambda_client.get_function(FunctionName=FUNCTION_NAME)
        function_arn = response["Configuration"]["FunctionArn"]
        lambda_client.update_function_code(FunctionName=FUNCTION_NAME, ZipFile=zip_bytes)
        wait_for_function_ready(lambda_client)
        lambda_client.update_function_configuration(
            FunctionName=FUNCTION_NAME,
            Role=role,
            Runtime=RUNTIME,
            Handler=HANDLER,
            Timeout=TIMEOUT_SECONDS,
            MemorySize=MEMORY_MB,
            Environment=environment,
            VpcConfig=vpc_config,
        )
        wait_for_function_ready(lambda_client)
        print(f"Updated Lambda function: {FUNCTION_NAME}")
        return function_arn
    except ClientError as error:
        if error.response["Error"]["Code"] != "ResourceNotFoundException":
            raise

    response = lambda_client.create_function(
        FunctionName=FUNCTION_NAME,
        Runtime=RUNTIME,
        Role=role,
        Handler=HANDLER,
        Code={"ZipFile": zip_bytes},
        Timeout=TIMEOUT_SECONDS,
        MemorySize=MEMORY_MB,
        Environment=environment,
        VpcConfig=vpc_config,
        Tags=TAGS,
    )
    wait_for_function_ready(lambda_client)
    print(f"Created Lambda function: {FUNCTION_NAME}")
    return response["FunctionArn"]


def ensure_event_source_mapping(lambda_client, *, stream_arn: str) -> None:
    mappings = lambda_client.list_event_source_mappings(
        EventSourceArn=stream_arn,
        FunctionName=FUNCTION_NAME,
    )["EventSourceMappings"]
    if mappings:
        mapping = mappings[0]
        lambda_client.update_event_source_mapping(
            UUID=mapping["UUID"],
            BatchSize=1,
            Enabled=True,
        )
        print(f"Updated Kinesis event source mapping: {mapping['UUID']}")
        return

    response = lambda_client.create_event_source_mapping(
        EventSourceArn=stream_arn,
        FunctionName=FUNCTION_NAME,
        StartingPosition="LATEST",
        BatchSize=1,
        Enabled=True,
    )
    print(f"Created Kinesis event source mapping: {response['UUID']}")


if __name__ == "__main__":
    region = resolve_region(AWS_REGION)
    session = boto3.Session(region_name=region)
    ec2_client = session.client("ec2")
    iam_client = session.client("iam")
    kinesis_client = session.client("kinesis")
    lambda_client = session.client("lambda")

    vpc_id = require_env("VPC_ID", placeholder_prefixes=("vpc-xxxxxxxx",))
    private_subnet_ids = require_csv_env("PRIVATE_SUBNET_IDS", min_values=1, placeholder_prefixes=("subnet-xxxxxxxx",))
    lambda_group_id = get_security_group_id(
        ec2_client,
        group_name=LAMBDA_SECURITY_GROUP_NAME,
        vpc_id=vpc_id,
    )
    if not lambda_group_id:
        raise RuntimeError(f"Security group {LAMBDA_SECURITY_GROUP_NAME} does not exist. Run create_security_groups.py.")

    private_ip = find_finbert_private_ip(ec2_client)
    api_url = f"http://{private_ip}:8000/predict"
    stream = kinesis_client.describe_stream_summary(StreamName=STREAM_NAME)["StreamDescriptionSummary"]
    function_arn = ensure_function(
        lambda_client,
        zip_bytes=package_lambda(),
        role=role_arn(iam_client),
        subnet_ids=private_subnet_ids,
        security_group_id=lambda_group_id,
        api_url=api_url,
    )
    ensure_event_source_mapping(lambda_client, stream_arn=stream["StreamARN"])

    print(f"Lambda function ready: {function_arn}")
    print(f"FinBERT API URL: {api_url}")
    print(f"Result prefix: s3://{RESULT_BUCKET}/{RESULT_PREFIX}/")
