#!/usr/bin/env python3
"""Create the FinBERT inference request Kinesis stream."""

from __future__ import annotations

from utils.aws import aws_client
from utils.common import resolve_region
from utils.kinesis import ensure_stream

AWS_REGION = "us-east-1"
STREAM_NAME = "proyecto-finbert-inference-requests"
TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "inference",
    "ManagedBy": "python-script",
}


if __name__ == "__main__":
    region = resolve_region(AWS_REGION)
    kinesis_client = aws_client("kinesis", region=region)
    description = ensure_stream(kinesis_client, stream_name=STREAM_NAME, tags=TAGS)

    print(f"Kinesis stream ready: {STREAM_NAME}")
    print(f"Stream ARN: {description['StreamARN']}")
