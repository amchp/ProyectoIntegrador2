"""Shared security group provisioning helpers."""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from utils.common import serialize_tags


def _named_tags(name: str, tags: dict[str, str]) -> list[dict[str, str]]:
    return serialize_tags({"Name": name, **tags})


def get_security_group_id(
    ec2_client,
    *,
    group_name: str,
    vpc_id: str,
) -> str | None:
    groups = ec2_client.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [group_name]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    )["SecurityGroups"]
    if not groups:
        return None
    return groups[0]["GroupId"]


def ensure_security_group(
    ec2_client,
    *,
    group_name: str,
    description: str,
    vpc_id: str,
    tags: dict[str, str],
) -> str:
    existing_group_id = get_security_group_id(
        ec2_client,
        group_name=group_name,
        vpc_id=vpc_id,
    )
    if existing_group_id:
        return existing_group_id

    response = ec2_client.create_security_group(
        GroupName=group_name,
        Description=description,
        VpcId=vpc_id,
        TagSpecifications=[
            {
                "ResourceType": "security-group",
                "Tags": _named_tags(group_name, tags),
            }
        ],
    )
    group_id = response["GroupId"]
    print(f"Created security group {group_name} ({group_id}).")
    return group_id


def ensure_ingress_cidr(
    ec2_client,
    *,
    group_id: str,
    protocol: str,
    from_port: int,
    to_port: int,
    cidr_ip: str,
    description: str,
) -> None:
    try:
        ec2_client.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[
                {
                    "IpProtocol": protocol,
                    "FromPort": from_port,
                    "ToPort": to_port,
                    "IpRanges": [{"CidrIp": cidr_ip, "Description": description}],
                }
            ],
        )
        print(f"Allowed {protocol}:{from_port}-{to_port} from {cidr_ip} on {group_id}.")
    except ClientError as error:
        if error.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise


def ensure_ingress_from_security_group(
    ec2_client,
    *,
    group_id: str,
    protocol: str,
    from_port: int,
    to_port: int,
    source_group_id: str,
    description: str,
) -> None:
    try:
        ec2_client.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[
                {
                    "IpProtocol": protocol,
                    "FromPort": from_port,
                    "ToPort": to_port,
                    "UserIdGroupPairs": [
                        {
                            "GroupId": source_group_id,
                            "Description": description,
                        }
                    ],
                }
            ],
        )
        print(f"Allowed {protocol}:{from_port}-{to_port} from {source_group_id} on {group_id}.")
    except ClientError as error:
        if error.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise


def ensure_self_ingress_all_traffic(
    ec2_client,
    *,
    group_id: str,
    description: str,
) -> None:
    try:
        ec2_client.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[
                {
                    "IpProtocol": "-1",
                    "UserIdGroupPairs": [
                        {
                            "GroupId": group_id,
                            "Description": description,
                        }
                    ],
                }
            ],
        )
        print(f"Allowed all traffic from {group_id} on itself.")
    except ClientError as error:
        if error.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise


def create_security_group_stack(
    *,
    region: str,
    vpc_id: str,
    ec2_group_name: str,
    db_group_name: str,
    lambda_group_name: str | None = None,
    ssh_allowed_cidr: str,
    api_allowed_cidr: str | None = None,
    api_port: int = 8000,
    tags: dict[str, str],
) -> dict[str, str]:
    session = boto3.Session(region_name=region)
    ec2_client = session.client("ec2")

    ec2_group_id = ensure_security_group(
        ec2_client,
        group_name=ec2_group_name,
        description="EC2 access for ProyectoDeGrado",
        vpc_id=vpc_id,
        tags=tags,
    )
    ensure_ingress_cidr(
        ec2_client,
        group_id=ec2_group_id,
        protocol="tcp",
        from_port=22,
        to_port=22,
        cidr_ip=ssh_allowed_cidr,
        description="SSH access",
    )
    if api_allowed_cidr:
        ensure_ingress_cidr(
            ec2_client,
            group_id=ec2_group_id,
            protocol="tcp",
            from_port=api_port,
            to_port=api_port,
            cidr_ip=api_allowed_cidr,
            description="FinBERT API access",
        )

    db_group_id = ensure_security_group(
        ec2_client,
        group_name=db_group_name,
        description="PostgreSQL access for ProyectoDeGrado",
        vpc_id=vpc_id,
        tags=tags,
    )
    ensure_ingress_from_security_group(
        ec2_client,
        group_id=db_group_id,
        protocol="tcp",
        from_port=5432,
        to_port=5432,
        source_group_id=ec2_group_id,
        description="PostgreSQL from EC2",
    )
    ensure_self_ingress_all_traffic(
        ec2_client,
        group_id=db_group_id,
        description="Glue workers and RDS access within the DB security group",
    )

    resources = {
        "ec2_security_group_id": ec2_group_id,
        "db_security_group_id": db_group_id,
    }
    if lambda_group_name:
        lambda_group_id = ensure_security_group(
            ec2_client,
            group_name=lambda_group_name,
            description="Lambda access for FinBERT inference",
            vpc_id=vpc_id,
            tags=tags,
        )
        ensure_ingress_from_security_group(
            ec2_client,
            group_id=ec2_group_id,
            protocol="tcp",
            from_port=api_port,
            to_port=api_port,
            source_group_id=lambda_group_id,
            description="FinBERT API access from Lambda",
        )
        resources["lambda_security_group_id"] = lambda_group_id

    return resources
