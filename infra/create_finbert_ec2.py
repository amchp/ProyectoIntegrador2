#!/usr/bin/env python3
"""Launch or start the shared FinBERT EC2 GPU instance."""

from __future__ import annotations

import json
import time

import boto3
from botocore.exceptions import ClientError

from utils.common import load_local_env, require_csv_env, require_env, resolve_region, serialize_tags

AWS_REGION = "us-east-1"
INSTANCE_NAME = "proyecto-finbert-ec2"
DEFAULT_INSTANCE_TYPE = "t3.micro"
ROOT_VOLUME_GB = 100
FEATURES_BUCKET = "proyecto-integrador-2-features-amce"
FEATURES_PREFIX = "features/financial_sentiment/model_features"
ARTIFACT_PREFIX = "models/finbert"
ROLE_NAME = "proyecto-finbert-ec2-role"
INSTANCE_PROFILE_NAME = "proyecto-finbert-ec2-profile"
LAB_INSTANCE_PROFILE_CANDIDATES = ["LabRole", "LabInstanceProfile"]
EC2_SECURITY_GROUP_NAME = "proyecto-integrador-ec2-sg"
ELASTIC_IP_NAME = "proyecto-finbert-ec2-eip"
TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "model-serving",
    "ManagedBy": "python-script",
}


def find_dlami_id(ec2_client, ssm_client) -> str:
    for parameter_name in [
        "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id",
        "/aws/service/deeplearning/ami/x86_64/pytorch-2.4-ubuntu-22.04/latest/ami-id",
        "/aws/service/deeplearning/ami/x86_64/pytorch-2.3-ubuntu-22.04/latest/ami-id",
    ]:
        try:
            return ssm_client.get_parameter(Name=parameter_name)["Parameter"]["Value"]
        except ClientError:
            continue

    images = ec2_client.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": ["Deep Learning*GPU*PyTorch*Ubuntu*22.04*"]},
            {"Name": "state", "Values": ["available"]},
            {"Name": "architecture", "Values": ["x86_64"]},
        ],
    )["Images"]
    if not images:
        raise RuntimeError(
            "Could not resolve a Deep Learning AMI. Set FINBERT_AMI_ID to a GPU-ready AMI id."
        )
    return sorted(images, key=lambda image: image["CreationDate"])[-1]["ImageId"]


def find_cpu_ami_id(ssm_client) -> str:
    return ssm_client.get_parameter(
        Name="/aws/service/ami-amazon-linux-latest/amzn2-ami-hvm-x86_64-gp2"
    )["Parameter"]["Value"]


def optional_env(name: str) -> str:
    import os

    load_local_env()
    return os.getenv(name, "").strip()


def resolve_instance_type() -> str:
    return optional_env("FINBERT_INSTANCE_TYPE") or DEFAULT_INSTANCE_TYPE


def is_gpu_instance_type(instance_type: str) -> bool:
    return instance_type.split(".", 1)[0] in {"g4dn", "g5", "g6", "p3", "p4", "p5"}


def resolve_ami_id(ec2_client, ssm_client, *, instance_type: str) -> str:
    configured = optional_env("FINBERT_AMI_ID")
    if configured:
        return configured
    if is_gpu_instance_type(instance_type):
        return find_dlami_id(ec2_client, ssm_client)
    return find_cpu_ami_id(ssm_client)


def default_ssh_user(instance_type: str) -> str:
    if optional_env("FINBERT_AMI_ID"):
        return "ubuntu" if is_gpu_instance_type(instance_type) else "ec2-user"
    return "ubuntu" if is_gpu_instance_type(instance_type) else "ec2-user"


def resolve_public_subnet_id(ec2_client, *, vpc_id: str) -> str:
    public_subnet_ids = optional_env("PUBLIC_SUBNET_IDS")
    if public_subnet_ids:
        return require_csv_env("PUBLIC_SUBNET_IDS", min_values=1, placeholder_prefixes=("subnet-xxxxxxxx",))[0]

    response = ec2_client.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "map-public-ip-on-launch", "Values": ["true"]},
            {"Name": "state", "Values": ["available"]},
        ]
    )
    subnets = sorted(response["Subnets"], key=lambda subnet: subnet["SubnetId"])
    if not subnets:
        raise ValueError(
            "Set PUBLIC_SUBNET_IDS in .env, or run create_vpc.py so the VPC has a public subnet "
            "with MapPublicIpOnLaunch enabled."
        )

    subnet_id = subnets[0]["SubnetId"]
    print(f"PUBLIC_SUBNET_IDS is not set. Using discovered public subnet: {subnet_id}")
    return subnet_id


def ensure_instance_profile(iam_client) -> str:
    profile_name = optional_env("FINBERT_INSTANCE_PROFILE_NAME")
    if profile_name:
        try:
            iam_client.get_instance_profile(InstanceProfileName=profile_name)
        except ClientError as error:
            code = error.response["Error"]["Code"]
            if code == "NoSuchEntity":
                raise RuntimeError(
                    f"FINBERT_INSTANCE_PROFILE_NAME is set to {profile_name}, but that instance profile "
                    "does not exist in this AWS account."
                ) from error
            raise
        print(f"Using existing EC2 instance profile from FINBERT_INSTANCE_PROFILE_NAME: {profile_name}")
        return profile_name

    for candidate in LAB_INSTANCE_PROFILE_CANDIDATES:
        try:
            iam_client.get_instance_profile(InstanceProfileName=candidate)
        except ClientError as error:
            if error.response["Error"]["Code"] == "NoSuchEntity":
                continue
            raise
        print(f"Using lab EC2 instance profile: {candidate}")
        return candidate

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    s3_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{FEATURES_BUCKET}"],
                "Condition": {
                    "StringLike": {
                        "s3:prefix": [
                            f"{FEATURES_PREFIX}/*",
                            f"{ARTIFACT_PREFIX}/*",
                        ]
                    }
                },
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{FEATURES_BUCKET}/{FEATURES_PREFIX}/*"],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": [f"arn:aws:s3:::{FEATURES_BUCKET}/{ARTIFACT_PREFIX}/*"],
            },
        ],
    }

    try:
        iam_client.get_role(RoleName=ROLE_NAME)
    except ClientError as error:
        if error.response["Error"]["Code"] != "NoSuchEntity":
            raise
        try:
            iam_client.create_role(
                RoleName=ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(trust_policy),
                Description="FinBERT EC2 access to financial sentiment S3 snapshots and artifacts.",
                Tags=serialize_tags(TAGS),
            )
        except ClientError as create_error:
            if create_error.response["Error"]["Code"] == "AccessDenied":
                raise RuntimeError(
                    "This AWS identity cannot create IAM roles. Set FINBERT_INSTANCE_PROFILE_NAME "
                    "in infra/.env to an existing EC2 instance profile that can read the feature "
                    "snapshot S3 prefix and write the models/finbert S3 prefix. In AWS Academy or "
                    "Vocareum labs, check whether the lab provides a pre-created profile such as "
                    "LabInstanceProfile, then rerun create_finbert_ec2.py."
                ) from create_error
            raise

    iam_client.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="FinbertS3ArtifactsAccess",
        PolicyDocument=json.dumps(s3_policy),
    )

    try:
        iam_client.get_instance_profile(InstanceProfileName=INSTANCE_PROFILE_NAME)
    except ClientError as error:
        if error.response["Error"]["Code"] != "NoSuchEntity":
            raise
        iam_client.create_instance_profile(
            InstanceProfileName=INSTANCE_PROFILE_NAME,
            Tags=serialize_tags(TAGS),
        )

    profile = iam_client.get_instance_profile(InstanceProfileName=INSTANCE_PROFILE_NAME)["InstanceProfile"]
    if not any(role["RoleName"] == ROLE_NAME for role in profile.get("Roles", [])):
        try:
            iam_client.add_role_to_instance_profile(
                InstanceProfileName=INSTANCE_PROFILE_NAME,
                RoleName=ROLE_NAME,
            )
            time.sleep(10)
        except ClientError as error:
            if error.response["Error"]["Code"] != "LimitExceeded":
                raise

    return INSTANCE_PROFILE_NAME


def get_security_group_id(ec2_client, *, vpc_id: str) -> str:
    groups = ec2_client.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [EC2_SECURITY_GROUP_NAME]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    )["SecurityGroups"]
    if not groups:
        raise RuntimeError(f"Security group {EC2_SECURITY_GROUP_NAME} does not exist. Run create_security_groups.py.")
    return groups[0]["GroupId"]


def find_existing_instance(ec2_client) -> dict | None:
    reservations = ec2_client.describe_instances(
        Filters=[
            {"Name": "tag:Name", "Values": [INSTANCE_NAME]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )["Reservations"]
    instances = [instance for reservation in reservations for instance in reservation["Instances"]]
    return instances[0] if instances else None


def wait_for_running(ec2_client, instance_id: str) -> dict:
    waiter = ec2_client.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])
    waiter = ec2_client.get_waiter("instance_status_ok")
    waiter.wait(InstanceIds=[instance_id])
    return ec2_client.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]


def find_finbert_elastic_ip(ec2_client) -> dict | None:
    addresses = ec2_client.describe_addresses(
        Filters=[{"Name": "tag:Name", "Values": [ELASTIC_IP_NAME]}]
    )["Addresses"]
    return addresses[0] if addresses else None


def associate_preallocated_elastic_ip(ec2_client, *, instance_id: str) -> str | None:
    address = find_finbert_elastic_ip(ec2_client)
    if not address:
        return None
    if address.get("InstanceId") == instance_id:
        return address["PublicIp"]

    allocation_id = address["AllocationId"]
    ec2_client.associate_address(
        AllocationId=allocation_id,
        InstanceId=instance_id,
        AllowReassociation=True,
    )
    refreshed = ec2_client.describe_addresses(AllocationIds=[allocation_id])["Addresses"][0]
    public_ip = refreshed["PublicIp"]
    print(f"Associated preallocated Elastic IP with instance: {public_ip}")
    return public_ip


if __name__ == "__main__":
    region = resolve_region(AWS_REGION)
    session = boto3.Session(region_name=region)
    ec2_client = session.client("ec2")
    iam_client = session.client("iam")
    ssm_client = session.client("ssm")

    vpc_id = require_env("VPC_ID", placeholder_prefixes=("vpc-xxxxxxxx",))
    public_subnet_id = resolve_public_subnet_id(ec2_client, vpc_id=vpc_id)
    key_name = require_env("EC2_KEY_NAME")
    security_group_id = get_security_group_id(ec2_client, vpc_id=vpc_id)
    instance_profile_name = ensure_instance_profile(iam_client)

    instance = find_existing_instance(ec2_client)
    if instance:
        instance_id = instance["InstanceId"]
        state = instance["State"]["Name"]
        if state == "stopped":
            ec2_client.start_instances(InstanceIds=[instance_id])
            print(f"Started existing FinBERT EC2 instance: {instance_id}")
        else:
            print(f"Reusing existing FinBERT EC2 instance: {instance_id} ({state})")
    else:
        instance_type = resolve_instance_type()
        ami_id = resolve_ami_id(ec2_client, ssm_client, instance_type=instance_type)
        print(f"Launching FinBERT EC2 instance type: {instance_type}")
        print(f"Using AMI: {ami_id}")
        response = ec2_client.run_instances(
            ImageId=ami_id,
            InstanceType=instance_type,
            MinCount=1,
            MaxCount=1,
            KeyName=key_name,
            SubnetId=public_subnet_id,
            SecurityGroupIds=[security_group_id],
            IamInstanceProfile={"Name": instance_profile_name},
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {
                        "VolumeSize": ROOT_VOLUME_GB,
                        "VolumeType": "gp3",
                        "DeleteOnTermination": True,
                    },
                }
            ],
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": serialize_tags({"Name": INSTANCE_NAME, **TAGS}),
                }
            ],
        )
        instance_id = response["Instances"][0]["InstanceId"]
        print(f"Launched FinBERT EC2 instance: {instance_id}")

    instance = wait_for_running(ec2_client, instance_id)
    elastic_ip = associate_preallocated_elastic_ip(ec2_client, instance_id=instance_id)
    if elastic_ip:
        instance = ec2_client.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]
    public_dns = instance.get("PublicDnsName", "")
    public_ip = elastic_ip or instance.get("PublicIpAddress", "")
    ssh_user = default_ssh_user(resolve_instance_type())
    print(f"Instance ready: {instance_id}")
    print(f"Public DNS: {public_dns}")
    print(f"Public IP: {public_ip}")
    print(f"SSH: ssh -i <key.pem> {ssh_user}@{public_dns or public_ip}")
    print(f"API: http://{public_ip}:8000")
