#!/usr/bin/env python3
"""Stop the shared FinBERT EC2 GPU instance."""

from __future__ import annotations

import boto3

from utils.common import resolve_region

AWS_REGION = "us-east-1"
INSTANCE_NAME = "proyecto-finbert-ec2"


if __name__ == "__main__":
    ec2_client = boto3.Session(region_name=resolve_region(AWS_REGION)).client("ec2")
    reservations = ec2_client.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [INSTANCE_NAME]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )["Reservations"]
    instances = [instance for reservation in reservations for instance in reservation["Instances"]]
    if not instances:
        print(f"No FinBERT EC2 instance found with Name={INSTANCE_NAME}.")
        raise SystemExit(0)

    instance_id = instances[0]["InstanceId"]
    state = instances[0]["State"]["Name"]
    if state == "stopped":
        print(f"FinBERT EC2 instance is already stopped: {instance_id}")
        raise SystemExit(0)

    ec2_client.stop_instances(InstanceIds=[instance_id])
    print(f"Stopping FinBERT EC2 instance: {instance_id}")
