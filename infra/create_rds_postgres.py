#!/usr/bin/env python3
"""Create a small PostgreSQL RDS instance with cost-conscious defaults."""

from __future__ import annotations

from utils.common import generate_secret, persist_env_values, require_csv_env, require_env, resolve_region

AWS_REGION = "us-east-1"
create_rds_instance = None
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


def resolve_master_password() -> str:
    try:
        return require_env("RDS_MASTER_PASSWORD")
    except ValueError:
        password = generate_secret()
        persist_env_values({"RDS_MASTER_PASSWORD": password}, secret_keys={"RDS_MASTER_PASSWORD"})
        return password


def main() -> None:
    global create_rds_instance
    if create_rds_instance is None:
        from utils.rds import create_rds_instance as create_rds_instance_impl

        create_rds_instance = create_rds_instance_impl

    region = resolve_region(AWS_REGION)
    master_password = resolve_master_password()
    create_rds_instance(
        region=region,
        db_instance_identifier=DB_INSTANCE_IDENTIFIER,
        db_name=DB_NAME,
        master_username=MASTER_USERNAME,
        master_password=master_password,
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
    persist_env_values({"AWS_REGION": region})


if __name__ == "__main__":
    main()
