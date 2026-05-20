#!/usr/bin/env python3
"""Create a small PostgreSQL RDS instance with cost-conscious defaults."""

from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError
from utils.common import require_csv_env, require_env, resolve_region, serialize_tags
from utils.security_groups import get_security_group_id

AWS_REGION = "us-east-1"
DB_INSTANCE_IDENTIFIER = "proyecto-postgres"
DB_NAME = "proyectodb"
MASTER_USERNAME = "postgres"
DB_SECURITY_GROUP_NAME = "proyecto-postgres-sg"
DB_INSTANCE_CLASS = "db.t3.micro"
ALLOCATED_STORAGE = 20
DB_PORT = 5432
BACKUP_RETENTION_DAYS = 1
PUBLICLY_ACCESSIBLE = False
WAIT_FOR_INSTANCE = True
DB_TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "process",
    "ManagedBy": "python-script",
}
MASTER_PASSWORD = os.getenv("RDS_MASTER_PASSWORD")


def ensure_subnet_group(
    rds_client,
    subnet_group_name: str,
    subnet_ids: list[str],
    tags: dict[str, str],
) -> None:
    if len(subnet_ids) < 2:
        raise ValueError("Provide at least two subnet IDs for the RDS subnet group.")

    try:
        rds_client.describe_db_subnet_groups(DBSubnetGroupName=subnet_group_name)
        print(f"Subnet group {subnet_group_name} already exists. Reusing it.")
        return
    except ClientError as error:
        if error.response["Error"]["Code"] != "DBSubnetGroupNotFoundFault":
            raise

    rds_client.create_db_subnet_group(
        DBSubnetGroupName=subnet_group_name,
        DBSubnetGroupDescription="Subnets for ProyectoDeGrado PostgreSQL",
        SubnetIds=subnet_ids,
        Tags=serialize_tags(tags),
    )
    print(f"Created subnet group {subnet_group_name}.")


def get_existing_instance(rds_client, identifier: str) -> dict | None:
    try:
        response = rds_client.describe_db_instances(DBInstanceIdentifier=identifier)
    except ClientError as error:
        if error.response["Error"]["Code"] in {"DBInstanceNotFound", "DBInstanceNotFoundFault"}:
            return None
        raise
    return response["DBInstances"][0]


def create_rds_instance(
    *,
    region: str,
    db_instance_identifier: str,
    db_name: str,
    master_username: str,
    master_password: str,
    vpc_id: str,
    subnet_ids: list[str],
    db_security_group_name: str,
    db_instance_class: str,
    allocated_storage: int,
    port: int,
    backup_retention_days: int,
    publicly_accessible: bool,
    wait_for_instance: bool,
    tags: dict[str, str],
) -> None:
    if allocated_storage < 20:
        raise ValueError("Allocated storage must be at least 20 GiB for PostgreSQL RDS.")

    session = boto3.Session(region_name=region)
    ec2_client = session.client("ec2")
    rds_client = session.client("rds")

    subnet_group_name = f"{db_instance_identifier}-subnet-group"
    existing = get_existing_instance(rds_client, db_instance_identifier)
    if existing:
        print(f"RDS instance {db_instance_identifier} already exists. Reusing it.")
        print_instance_summary(existing)
        return

    security_group_id = get_security_group_id(
        ec2_client=ec2_client,
        group_name=db_security_group_name,
        vpc_id=vpc_id,
    )
    if not security_group_id:
        raise ValueError(
            f"Security group {db_security_group_name} was not found in VPC {vpc_id}. "
            "Run create_security_groups.py first."
        )
    ensure_subnet_group(
        rds_client=rds_client,
        subnet_group_name=subnet_group_name,
        subnet_ids=subnet_ids,
        tags=tags,
    )

    rds_client.create_db_instance(
        DBInstanceIdentifier=db_instance_identifier,
        DBName=db_name,
        Engine="postgres",
        MasterUsername=master_username,
        MasterUserPassword=master_password,
        DBInstanceClass=db_instance_class,
        AllocatedStorage=allocated_storage,
        StorageType="gp3",
        StorageEncrypted=True,
        Port=port,
        BackupRetentionPeriod=backup_retention_days,
        PubliclyAccessible=publicly_accessible,
        MultiAZ=False,
        AutoMinorVersionUpgrade=True,
        CopyTagsToSnapshot=True,
        DeletionProtection=False,
        EnablePerformanceInsights=False,
        MonitoringInterval=0,
        DBSubnetGroupName=subnet_group_name,
        VpcSecurityGroupIds=[security_group_id],
        Tags=serialize_tags(tags),
    )
    print(f"Started creation of RDS instance {db_instance_identifier}.")

    if wait_for_instance:
        print("Waiting for the RDS instance to become available.")
        waiter = rds_client.get_waiter("db_instance_available")
        waiter.wait(
            DBInstanceIdentifier=db_instance_identifier,
            WaiterConfig={"Delay": 30, "MaxAttempts": 60},
        )
        instance = rds_client.describe_db_instances(
            DBInstanceIdentifier=db_instance_identifier
        )["DBInstances"][0]
        print_instance_summary(instance)
    else:
        print("Set WAIT_FOR_INSTANCE = True if you want to block until the endpoint is ready.")


def print_instance_summary(instance: dict) -> None:
    endpoint = instance.get("Endpoint", {})
    address = endpoint.get("Address", "pending")
    port = endpoint.get("Port", "pending")
    print(f"RDS ready: {instance['DBInstanceIdentifier']}")
    print(f"Status: {instance['DBInstanceStatus']}")
    print(f"Endpoint: {address}:{port}")


if __name__ == "__main__":
    if not MASTER_PASSWORD:
        raise ValueError("Set the RDS_MASTER_PASSWORD environment variable before running this script.")

    create_rds_instance(
        region=resolve_region(AWS_REGION),
        db_instance_identifier=DB_INSTANCE_IDENTIFIER,
        db_name=DB_NAME,
        master_username=MASTER_USERNAME,
        master_password=MASTER_PASSWORD,
        vpc_id=require_env("VPC_ID", placeholder_prefixes=("vpc-xxxxxxxx",)),
        subnet_ids=require_csv_env(
            "PRIVATE_SUBNET_IDS",
            min_values=2,
            placeholder_prefixes=("subnet-aaaaaaaa", "subnet-bbbbbbbb"),
        ),
        db_security_group_name=DB_SECURITY_GROUP_NAME,
        db_instance_class=DB_INSTANCE_CLASS,
        allocated_storage=ALLOCATED_STORAGE,
        port=DB_PORT,
        backup_retention_days=BACKUP_RETENTION_DAYS,
        publicly_accessible=PUBLICLY_ACCESSIBLE,
        wait_for_instance=WAIT_FOR_INSTANCE,
        tags=DB_TAGS,
    )
