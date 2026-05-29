#!/usr/bin/env python3
"""Allocate and associate a stable public Elastic IP for the FinBERT EC2 instance."""

from __future__ import annotations

from utils.aws import aws_client
from utils.common import persist_env_values, resolve_region
from utils.ec2 import associate_elastic_ip, ensure_elastic_ip, find_instance_by_name

AWS_REGION = "us-east-1"
INSTANCE_NAME = "proyecto-finbert-ec2"
ELASTIC_IP_NAME = "proyecto-finbert-ec2-eip"
TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "model-serving",
    "ManagedBy": "python-script",
}


def main() -> None:
    ec2_client = aws_client("ec2", region=resolve_region(AWS_REGION))
    address = ensure_elastic_ip(ec2_client, name=ELASTIC_IP_NAME, tags=TAGS)
    instance = find_instance_by_name(ec2_client, name=INSTANCE_NAME)
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
    persist_env_values(
        {
            "FINBERT_ELASTIC_IP": public_ip,
            "FINBERT_ELASTIC_IP_ALLOCATION_ID": address["AllocationId"],
        }
    )
    print(f"Elastic IP ready: {public_ip}")
    print(f"SSH: ssh -i <key.pem> ubuntu@{public_ip}")
    print(f"API: http://{public_ip}:8000")


if __name__ == "__main__":
    main()
