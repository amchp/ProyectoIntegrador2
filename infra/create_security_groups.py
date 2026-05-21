#!/usr/bin/env python3
"""Create the shared EC2 and PostgreSQL security groups."""

from __future__ import annotations

from urllib.request import urlopen

from utils.common import load_local_env, require_env, resolve_region
from utils.security_groups import create_security_group_stack

AWS_REGION = "us-east-1"
EC2_SECURITY_GROUP_NAME = "proyecto-integrador-ec2-sg"
DB_SECURITY_GROUP_NAME = "proyecto-postgres-sg"
LAMBDA_SECURITY_GROUP_NAME = "proyecto-finbert-lambda-sg"
API_PORT = 8000
SECURITY_GROUP_TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "network",
    "ManagedBy": "python-script",
}


def optional_env(name: str) -> str:
    import os

    load_local_env()
    return os.getenv(name, "").strip()


def current_public_ip_cidr() -> str:
    with urlopen("https://checkip.amazonaws.com", timeout=10) as response:
        ip_address = response.read().decode("utf-8").strip()
    if not ip_address:
        raise ValueError("Could not detect the current public IP address.")
    return f"{ip_address}/32"


def resolve_allowed_cidr(name: str) -> str:
    configured = optional_env(name)
    if configured:
        return configured
    detected = current_public_ip_cidr()
    print(f"{name} is not set. Using current public IP: {detected}")
    return detected


if __name__ == "__main__":
    resources = create_security_group_stack(
        region=resolve_region(AWS_REGION),
        vpc_id=require_env("VPC_ID", placeholder_prefixes=("vpc-xxxxxxxx",)),
        ec2_group_name=EC2_SECURITY_GROUP_NAME,
        db_group_name=DB_SECURITY_GROUP_NAME,
        lambda_group_name=LAMBDA_SECURITY_GROUP_NAME,
        ssh_allowed_cidr=resolve_allowed_cidr("SSH_ALLOWED_CIDR"),
        api_allowed_cidr=resolve_allowed_cidr("API_ALLOWED_CIDR"),
        api_port=API_PORT,
        tags=SECURITY_GROUP_TAGS,
    )
    print(f"EC2 security group ready: {resources['ec2_security_group_id']}")
    print(f"Postgres security group ready: {resources['db_security_group_id']}")
    print(f"Lambda security group ready: {resources['lambda_security_group_id']}")
