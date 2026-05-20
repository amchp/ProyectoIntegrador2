#!/usr/bin/env python3
"""Create the shared EC2 and PostgreSQL security groups."""

from __future__ import annotations

from utils.common import require_env, resolve_region
from utils.security_groups import create_security_group_stack

AWS_REGION = "us-east-1"
EC2_SECURITY_GROUP_NAME = "proyecto-integrador-ec2-sg"
DB_SECURITY_GROUP_NAME = "proyecto-postgres-sg"
SSH_ALLOWED_CIDR = "0.0.0.0/0"
SECURITY_GROUP_TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "network",
    "ManagedBy": "python-script",
}


if __name__ == "__main__":
    resources = create_security_group_stack(
        region=resolve_region(AWS_REGION),
        vpc_id=require_env("VPC_ID", placeholder_prefixes=("vpc-xxxxxxxx",)),
        ec2_group_name=EC2_SECURITY_GROUP_NAME,
        db_group_name=DB_SECURITY_GROUP_NAME,
        ssh_allowed_cidr=SSH_ALLOWED_CIDR,
        tags=SECURITY_GROUP_TAGS,
    )
    print(f"EC2 security group ready: {resources['ec2_security_group_id']}")
    print(f"Postgres security group ready: {resources['db_security_group_id']}")
