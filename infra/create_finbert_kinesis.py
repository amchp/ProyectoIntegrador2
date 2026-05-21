#!/usr/bin/env python3
"""Create the FinBERT inference request Kinesis stream."""

from __future__ import annotations

import time

import boto3
from botocore.exceptions import ClientError

from utils.common import resolve_region

AWS_REGION = "us-east-1"
STREAM_NAME = "proyecto-finbert-inference-requests"
TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "inference",
    "ManagedBy": "python-script",
}


def wait_for_active(kinesis_client, *, stream_name: str) -> dict:
    while True:
        description = kinesis_client.describe_stream_summary(StreamName=stream_name)["StreamDescriptionSummary"]
        if description["StreamStatus"] == "ACTIVE":
            return description
        print(f"Waiting for Kinesis stream to become ACTIVE: {description['StreamStatus']}")
        time.sleep(5)


if __name__ == "__main__":
    region = resolve_region(AWS_REGION)
    kinesis_client = boto3.Session(region_name=region).client("kinesis")

    try:
        description = kinesis_client.describe_stream_summary(StreamName=STREAM_NAME)["StreamDescriptionSummary"]
        print(f"Reusing Kinesis stream: {STREAM_NAME} ({description['StreamStatus']})")
    except ClientError as error:
        if error.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        kinesis_client.create_stream(
            StreamName=STREAM_NAME,
            StreamModeDetails={"StreamMode": "ON_DEMAND"},
        )
        print(f"Created Kinesis stream: {STREAM_NAME}")
        description = wait_for_active(kinesis_client, stream_name=STREAM_NAME)
        try:
            kinesis_client.add_tags_to_stream(StreamName=STREAM_NAME, Tags=TAGS)
        except ClientError as tag_error:
            print(f"Could not tag Kinesis stream: {tag_error.response['Error']['Code']}")

    if description["StreamStatus"] != "ACTIVE":
        description = wait_for_active(kinesis_client, stream_name=STREAM_NAME)

    print(f"Kinesis stream ready: {STREAM_NAME}")
    print(f"Stream ARN: {description['StreamARN']}")
