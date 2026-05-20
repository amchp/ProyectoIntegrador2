#!/usr/bin/env python3
"""Create a low-cost VPC with public EC2 subnets and private DB subnets."""

from __future__ import annotations

from utils.common import resolve_region
from utils.network import create_vpc_stack

AWS_REGION = "us-east-1"
VPC_NAME = "proyecto-vpc"
VPC_CIDR_BLOCK = "10.0.0.0/16"
PUBLIC_SUBNETS = [
    {
        "name": "proyecto-public-subnet-a",
        "cidr_block": "10.0.1.0/24",
        "availability_zone": "us-east-1a",
    },
    {
        "name": "proyecto-public-subnet-b",
        "cidr_block": "10.0.2.0/24",
        "availability_zone": "us-east-1b",
    },
]
PRIVATE_SUBNETS = [
    {
        "name": "proyecto-private-subnet-a",
        "cidr_block": "10.0.11.0/24",
        "availability_zone": "us-east-1a",
    },
    {
        "name": "proyecto-private-subnet-b",
        "cidr_block": "10.0.12.0/24",
        "availability_zone": "us-east-1b",
    },
]
VPC_TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "network",
    "ManagedBy": "python-script",
}


if __name__ == "__main__":
    resources = create_vpc_stack(
        region=resolve_region(AWS_REGION),
        vpc_name=VPC_NAME,
        vpc_cidr_block=VPC_CIDR_BLOCK,
        public_subnets=PUBLIC_SUBNETS,
        private_subnets=PRIVATE_SUBNETS,
        tags=VPC_TAGS,
    )
    print(f"VPC ready: {resources['vpc_id']}")
    print(f"Public subnets for EC2 public IPs: {', '.join(resources['public_subnet_ids'])}")
    print(f"Private subnets for RDS: {', '.join(resources['private_subnet_ids'])}")
    print(f"S3 gateway endpoint for private subnets: {resources['s3_gateway_endpoint_id']}")
