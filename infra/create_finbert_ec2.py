#!/usr/bin/env python3
"""Launch or start the shared FinBERT EC2 GPU instance."""

from __future__ import annotations

from utils.aws import aws_clients, lab_instance_profile_name
from utils.common import load_local_env, persist_env_values, require_csv_env, require_env, resolve_region
from utils.ec2 import (
    associate_preallocated_elastic_ip,
    ensure_instance,
    find_public_subnet_id,
    find_instance_by_name,
    is_gpu_instance_type,
    resolve_ami_id as resolve_ec2_ami_id,
    wait_for_running,
)
from utils.security_groups import get_security_group_id as find_security_group_id

AWS_REGION = "us-east-1"
INSTANCE_NAME = "proyecto-finbert-ec2"
DEFAULT_INSTANCE_TYPE = "t3.micro"
ROOT_VOLUME_GB = 100
EC2_SECURITY_GROUP_NAME = "proyecto-integrador-ec2-sg"
ELASTIC_IP_NAME = "proyecto-finbert-ec2-eip"
TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "model-serving",
    "ManagedBy": "python-script",
}


def optional_env(name: str) -> str:
    import os

    load_local_env()
    return os.getenv(name, "").strip()


def resolve_instance_type() -> str:
    return optional_env("FINBERT_INSTANCE_TYPE") or DEFAULT_INSTANCE_TYPE


def resolve_ami_id(ec2_client, ssm_client, *, instance_type: str) -> str:
    return resolve_ec2_ami_id(
        ec2_client,
        ssm_client,
        instance_type=instance_type,
        configured_ami_id=optional_env("FINBERT_AMI_ID"),
    )


def default_ssh_user(instance_type: str) -> str:
    if optional_env("FINBERT_AMI_ID"):
        return "ubuntu" if is_gpu_instance_type(instance_type) else "ec2-user"
    return "ubuntu" if is_gpu_instance_type(instance_type) else "ec2-user"


def resolve_public_subnet_id(ec2_client, *, vpc_id: str) -> str:
    public_subnet_ids = optional_env("PUBLIC_SUBNET_IDS")
    if public_subnet_ids:
        return require_csv_env("PUBLIC_SUBNET_IDS", min_values=1, placeholder_prefixes=("subnet-xxxxxxxx",))[0]

    subnet_id = find_public_subnet_id(ec2_client, vpc_id=vpc_id)
    if not subnet_id:
        raise ValueError(
            "Set PUBLIC_SUBNET_IDS in .env, or run create_vpc.py so the VPC has a public subnet "
            "with MapPublicIpOnLaunch enabled."
        )

    print(f"PUBLIC_SUBNET_IDS is not set. Using discovered public subnet: {subnet_id}")
    return subnet_id


def find_existing_instance(ec2_client) -> dict | None:
    return find_instance_by_name(ec2_client, name=INSTANCE_NAME)


def main() -> None:
    region = resolve_region(AWS_REGION)
    ec2_client, iam_client, ssm_client = aws_clients(region, "ec2", "iam", "ssm")

    vpc_id = require_env("VPC_ID", placeholder_prefixes=("vpc-xxxxxxxx",))
    public_subnet_id = resolve_public_subnet_id(ec2_client, vpc_id=vpc_id)
    key_name = require_env("EC2_KEY_NAME")
    security_group_id = find_security_group_id(
        ec2_client,
        group_name=EC2_SECURITY_GROUP_NAME,
        vpc_id=vpc_id,
    )
    if not security_group_id:
        raise RuntimeError(f"Security group {EC2_SECURITY_GROUP_NAME} does not exist. Run create_security_groups.py.")
    instance_profile_name = lab_instance_profile_name(iam_client)
    print(f"Using lab EC2 instance profile: {instance_profile_name}")

    instance_type = resolve_instance_type()
    ami_id = resolve_ami_id(ec2_client, ssm_client, instance_type=instance_type)
    if not find_existing_instance(ec2_client):
        print(f"Launching FinBERT EC2 instance type: {instance_type}")
        print(f"Using AMI: {ami_id}")
    instance_id = ensure_instance(
        ec2_client,
        name=INSTANCE_NAME,
        tags=TAGS,
        display_name="FinBERT EC2 instance",
        run_instances_kwargs={
            "ImageId": ami_id,
            "InstanceType": instance_type,
            "MinCount": 1,
            "MaxCount": 1,
            "KeyName": key_name,
            "SubnetId": public_subnet_id,
            "SecurityGroupIds": [security_group_id],
            "IamInstanceProfile": {"Name": instance_profile_name},
            "BlockDeviceMappings": [
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {
                        "VolumeSize": ROOT_VOLUME_GB,
                        "VolumeType": "gp3",
                        "DeleteOnTermination": True,
                    },
                }
            ],
        },
    )

    instance = wait_for_running(ec2_client, instance_id)
    elastic_ip = associate_preallocated_elastic_ip(
        ec2_client,
        elastic_ip_name=ELASTIC_IP_NAME,
        instance_id=instance_id,
    )
    if elastic_ip:
        instance = ec2_client.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
    public_dns = instance.get("PublicDnsName", "")
    public_ip = elastic_ip or instance.get("PublicIpAddress", "")
    ssh_user = default_ssh_user(instance_type)
    persist_env_values(
        {
            "AWS_REGION": region,
            "FINBERT_INSTANCE_TYPE": instance_type,
            "FINBERT_INSTANCE_ID": instance_id,
            "FINBERT_PUBLIC_DNS": public_dns,
            "FINBERT_PUBLIC_IP": public_ip,
            "FINBERT_API_URL": f"http://{public_ip}:8000",
            "FINBERT_SSH_USER": ssh_user,
        }
    )
    print(f"Instance ready: {instance_id}")
    print(f"Public DNS: {public_dns}")
    print(f"Public IP: {public_ip}")
    print(f"SSH: ssh -i <key.pem> {ssh_user}@{public_dns or public_ip}")
    print(f"API: http://{public_ip}:8000")


if __name__ == "__main__":
    main()
