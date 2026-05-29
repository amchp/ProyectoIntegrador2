#!/usr/bin/env python3
"""Create or update the AWS Glue JDBC connection for the PostgreSQL RDS instance."""

from __future__ import annotations

from utils.common import require_csv_env, require_env, resolve_region
from utils.glue import create_or_update_glue_connection

AWS_REGION = "us-east-1"
CONNECTION_NAME = "Proyecto Financial Sentiment RDS connection"
DB_INSTANCE_IDENTIFIER = "proyecto-postgres"
DB_NAME = "proyectodb"
DB_USERNAME = "postgres"
DB_SECURITY_GROUP_NAME = "proyecto-postgres-sg"
JDBC_DRIVER_CLASS_NAME = "org.postgresql.Driver"


def main() -> None:
    try:
        master_password = require_env("RDS_MASTER_PASSWORD")
    except ValueError as error:
        raise ValueError(
            "Run create_rds_postgres.py first so RDS_MASTER_PASSWORD is generated in infra/.env."
        ) from error
    create_or_update_glue_connection(
        region=resolve_region(AWS_REGION),
        connection_name=CONNECTION_NAME,
        db_instance_identifier=DB_INSTANCE_IDENTIFIER,
        db_name=DB_NAME,
        db_username=DB_USERNAME,
        db_password=master_password,
        vpc_id=require_env("VPC_ID", placeholder_prefixes=("vpc-xxxxxxxx",)),
        subnet_ids=require_csv_env(
            "PRIVATE_SUBNET_IDS",
            min_values=2,
            placeholder_prefixes=("subnet-aaaaaaaa", "subnet-bbbbbbbb"),
        ),
        db_security_group_name=DB_SECURITY_GROUP_NAME,
        jdbc_driver_class_name=JDBC_DRIVER_CLASS_NAME,
    )


if __name__ == "__main__":
    main()
