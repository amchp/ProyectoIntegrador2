#!/usr/bin/env python3
"""Allocate and associate a stable public Elastic IP for the FinBERT EC2 instance."""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from utils.common import resolve_region, serialize_tags

AWS_REGION = "us-east-1"
INSTANCE_NAME = "proyecto-finbert-ec2"
ELASTIC_IP_NAME = "proyecto-finbert-ec2-eip"
TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "model-serving",
    "ManagedBy": "python-script",
}


def find_finbert_instance(ec2_client) -> dict:
    reservations = ec2_client.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [INSTANCE_NAME]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )["Reservations"]
    instances = [instance for reservation in reservations for instance in reservation["Instances"]]
    if not instances:
        raise RuntimeError(f"No FinBERT EC2 instance found with Name={INSTANCE_NAME}. Run create_finbert_ec2.py first.")
    return instances[0]


def find_finbert_instance_or_none(ec2_client) -> dict | None:
    reservations = ec2_client.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [INSTANCE_NAME]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )["Reservations"]
    instances = [instance for reservation in reservations for instance in reservation["Instances"]]
    return instances[0] if instances else None


def find_elastic_ip(ec2_client) -> dict | None:
    addresses = ec2_client.describe_addresses(
        Filters=[{"Name": "tag:Name", "Values": [ELASTIC_IP_NAME]}]
    )["Addresses"]
    return addresses[0] if addresses else None


def ensure_elastic_ip(ec2_client) -> dict:
    existing = find_elastic_ip(ec2_client)
    if existing:
        return existing

    response = ec2_client.allocate_address(
        Domain="vpc",
        TagSpecifications=[
            {
                "ResourceType": "elastic-ip",
                "Tags": serialize_tags({"Name": ELASTIC_IP_NAME, **TAGS}),
            }
        ],
    )
    print(f"Allocated Elastic IP: {response['PublicIp']} ({response['AllocationId']})")
    return response


def associate_elastic_ip(ec2_client, *, allocation_id: str, instance_id: str) -> None:
    address = ec2_client.describe_addresses(AllocationIds=[allocation_id])["Addresses"][0]
    if address.get("InstanceId") == instance_id:
        print(f"Elastic IP is already associated with {instance_id}.")
        return
    if "AssociationId" in address:
        ec2_client.disassociate_address(AssociationId=address["AssociationId"])
        print(f"Disassociated Elastic IP from {address.get('InstanceId', 'previous resource')}.")

    try:
        ec2_client.associate_address(
            AllocationId=allocation_id,
            InstanceId=instance_id,
            AllowReassociation=True,
        )
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code == "IncorrectInstanceState":
            raise RuntimeError(
                "The FinBERT instance is not in a state that can receive an Elastic IP. "
                "Start it with create_finbert_ec2.py, then rerun this script."
            ) from error
        raise
    print(f"Associated Elastic IP with instance: {instance_id}")


if __name__ == "__main__":
    ec2_client = boto3.Session(region_name=resolve_region(AWS_REGION)).client("ec2")
    address = ensure_elastic_ip(ec2_client)
    instance = find_finbert_instance_or_none(ec2_client)
    if instance:
        associate_elastic_ip(
            ec2_client,
            allocation_id=address["AllocationId"],
            instance_id=instance["InstanceId"],
        )
    else:
        print(
            f"No FinBERT EC2 instance found with Name={INSTANCE_NAME}. "
            "The Elastic IP is allocated and create_finbert_ec2.py will associate it after launch."
        )
    refreshed = ec2_client.describe_addresses(AllocationIds=[address["AllocationId"]])["Addresses"][0]
    public_ip = refreshed["PublicIp"]
    print(f"Elastic IP ready: {public_ip}")
    print(f"SSH: ssh -i <key.pem> ubuntu@{public_ip}")
    print(f"API: http://{public_ip}:8000")
