#!/usr/bin/env python3
"""Create or update the AWS Glue JDBC connection for the PostgreSQL RDS instance."""

from __future__ import annotations

import os

import boto3
from botocore.exceptions import ClientError
from utils.common import require_csv_env, require_env, resolve_region
from utils.security_groups import get_security_group_id

AWS_REGION = "us-east-1"
CONNECTION_NAME = "Proyecto Financial Sentiment RDS connection"
DB_INSTANCE_IDENTIFIER = "proyecto-postgres"
DB_NAME = "proyectodb"
DB_USERNAME = "postgres"
DB_SECURITY_GROUP_NAME = "proyecto-postgres-sg"
JDBC_DRIVER_CLASS_NAME = "org.postgresql.Driver"
MASTER_PASSWORD = os.getenv("RDS_MASTER_PASSWORD")


def get_rds_endpoint(rds_client, db_instance_identifier: str) -> tuple[str, int]:
    response = rds_client.describe_db_instances(
        DBInstanceIdentifier=db_instance_identifier,
    )
    instance = response["DBInstances"][0]
    if instance["DBInstanceStatus"] != "available":
        raise ValueError(
            f"RDS instance {db_instance_identifier} is {instance['DBInstanceStatus']}. "
            "Wait until it is available before creating the Glue connection."
        )

    endpoint = instance.get("Endpoint", {})
    address = endpoint.get("Address")
    port = endpoint.get("Port")
    if not address or not port:
        raise ValueError(f"RDS instance {db_instance_identifier} does not have an endpoint yet.")
    return address, port


def get_subnet_availability_zone(ec2_client, subnet_id: str) -> str:
    response = ec2_client.describe_subnets(SubnetIds=[subnet_id])
    return response["Subnets"][0]["AvailabilityZone"]


def glue_connection_exists(glue_client, connection_name: str) -> bool:
    try:
        glue_client.get_connection(Name=connection_name)
        return True
    except ClientError as error:
        if error.response["Error"]["Code"] == "EntityNotFoundException":
            return False
        raise


def create_or_update_glue_connection(
    *,
    region: str,
    connection_name: str,
    db_instance_identifier: str,
    db_name: str,
    db_username: str,
    db_password: str,
    vpc_id: str,
    subnet_ids: list[str],
    db_security_group_name: str,
) -> None:
    session = boto3.Session(region_name=region)
    ec2_client = session.client("ec2")
    rds_client = session.client("rds")
    glue_client = session.client("glue")

    subnet_id = subnet_ids[0]
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

    endpoint, port = get_rds_endpoint(rds_client, db_instance_identifier)
    jdbc_url = f"jdbc:postgresql://{endpoint}:{port}/{db_name}"
    connection_input = {
        "Name": connection_name,
        "Description": "JDBC connection to Proyecto financial sentiment PostgreSQL RDS",
        "ConnectionType": "JDBC",
        "ConnectionProperties": {
            "JDBC_CONNECTION_URL": jdbc_url,
            "USERNAME": db_username,
            "PASSWORD": db_password,
            "JDBC_DRIVER_CLASS_NAME": JDBC_DRIVER_CLASS_NAME,
        },
        "PhysicalConnectionRequirements": {
            "AvailabilityZone": get_subnet_availability_zone(ec2_client, subnet_id),
            "SecurityGroupIdList": [security_group_id],
            "SubnetId": subnet_id,
        },
    }

    if glue_connection_exists(glue_client, connection_name):
        glue_client.update_connection(
            Name=connection_name,
            ConnectionInput=connection_input,
        )
        print(f"Updated Glue connection: {connection_name}")
        return

    glue_client.create_connection(ConnectionInput=connection_input)
    print(f"Created Glue connection: {connection_name}")


if __name__ == "__main__":
    if not MASTER_PASSWORD:
        raise ValueError("Set the RDS_MASTER_PASSWORD environment variable before running this script.")

    create_or_update_glue_connection(
        region=resolve_region(AWS_REGION),
        connection_name=CONNECTION_NAME,
        db_instance_identifier=DB_INSTANCE_IDENTIFIER,
        db_name=DB_NAME,
        db_username=DB_USERNAME,
        db_password=MASTER_PASSWORD,
        vpc_id=require_env("VPC_ID", placeholder_prefixes=("vpc-xxxxxxxx",)),
        subnet_ids=require_csv_env(
            "PRIVATE_SUBNET_IDS",
            min_values=2,
            placeholder_prefixes=("subnet-aaaaaaaa", "subnet-bbbbbbbb"),
        ),
        db_security_group_name=DB_SECURITY_GROUP_NAME,
    )
