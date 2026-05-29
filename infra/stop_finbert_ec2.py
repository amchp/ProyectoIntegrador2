#!/usr/bin/env python3
"""Stop the shared FinBERT EC2 GPU instance."""

from __future__ import annotations

from utils.aws import aws_client
from utils.common import resolve_region
from utils.ec2 import find_instance_by_name

AWS_REGION = "us-east-1"
INSTANCE_NAME = "proyecto-finbert-ec2"


if __name__ == "__main__":
    ec2_client = aws_client("ec2", region=resolve_region(AWS_REGION))
    instance = find_instance_by_name(ec2_client, name=INSTANCE_NAME)
    if not instance:
        print(f"No FinBERT EC2 instance found with Name={INSTANCE_NAME}.")
        raise SystemExit(0)

    instance_id = instance["InstanceId"]
    state = instance["State"]["Name"]
    if state == "stopped":
        print(f"FinBERT EC2 instance is already stopped: {instance_id}")
        raise SystemExit(0)

    ec2_client.stop_instances(InstanceIds=[instance_id])
    print(f"Stopping FinBERT EC2 instance: {instance_id}")
