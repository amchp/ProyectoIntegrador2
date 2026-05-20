"""Shared VPC and subnet provisioning helpers."""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from utils.common import serialize_tags


def _with_name(tags: dict[str, str], name: str) -> list[dict[str, str]]:
    named_tags = {"Name": name, **tags}
    return serialize_tags(named_tags)


def _first_resource(resources: list[dict]) -> dict | None:
    return resources[0] if resources else None


def ensure_vpc(
    ec2_client,
    *,
    name: str,
    cidr_block: str,
    tags: dict[str, str],
) -> str:
    existing = ec2_client.describe_vpcs(
        Filters=[{"Name": "tag:Name", "Values": [name]}]
    )["Vpcs"]
    vpc = _first_resource(existing)
    if vpc:
        return vpc["VpcId"]

    response = ec2_client.create_vpc(
        CidrBlock=cidr_block,
        TagSpecifications=[
            {"ResourceType": "vpc", "Tags": _with_name(tags, name)},
        ],
    )
    vpc_id = response["Vpc"]["VpcId"]
    ec2_client.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2_client.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    print(f"Created VPC {name} ({vpc_id}).")
    return vpc_id


def ensure_internet_gateway(
    ec2_client,
    *,
    vpc_id: str,
    name: str,
    tags: dict[str, str],
) -> str:
    existing = ec2_client.describe_internet_gateways(
        Filters=[{"Name": "tag:Name", "Values": [name]}]
    )["InternetGateways"]
    internet_gateway = _first_resource(existing)
    if internet_gateway:
        internet_gateway_id = internet_gateway["InternetGatewayId"]
    else:
        response = ec2_client.create_internet_gateway(
            TagSpecifications=[
                {
                    "ResourceType": "internet-gateway",
                    "Tags": _with_name(tags, name),
                }
            ]
        )
        internet_gateway_id = response["InternetGateway"]["InternetGatewayId"]
        print(f"Created internet gateway {name} ({internet_gateway_id}).")

    attachments = internet_gateway.get("Attachments", []) if internet_gateway else []
    if not any(attachment["VpcId"] == vpc_id for attachment in attachments):
        try:
            ec2_client.attach_internet_gateway(
                InternetGatewayId=internet_gateway_id,
                VpcId=vpc_id,
            )
            print(f"Attached internet gateway {internet_gateway_id} to {vpc_id}.")
        except ClientError as error:
            if error.response["Error"]["Code"] != "Resource.AlreadyAssociated":
                raise

    return internet_gateway_id


def ensure_route_table(
    ec2_client,
    *,
    vpc_id: str,
    name: str,
    tags: dict[str, str],
) -> str:
    existing = ec2_client.describe_route_tables(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "tag:Name", "Values": [name]},
        ]
    )["RouteTables"]
    route_table = _first_resource(existing)
    if route_table:
        return route_table["RouteTableId"]

    response = ec2_client.create_route_table(
        VpcId=vpc_id,
        TagSpecifications=[
            {"ResourceType": "route-table", "Tags": _with_name(tags, name)},
        ],
    )
    route_table_id = response["RouteTable"]["RouteTableId"]
    print(f"Created route table {name} ({route_table_id}).")
    return route_table_id


def ensure_public_route(
    ec2_client,
    *,
    route_table_id: str,
    internet_gateway_id: str,
) -> None:
    route_table = ec2_client.describe_route_tables(RouteTableIds=[route_table_id])["RouteTables"][0]
    for route in route_table.get("Routes", []):
        if route.get("DestinationCidrBlock") != "0.0.0.0/0":
            continue
        if route.get("GatewayId") == internet_gateway_id:
            return
        ec2_client.replace_route(
            RouteTableId=route_table_id,
            DestinationCidrBlock="0.0.0.0/0",
            GatewayId=internet_gateway_id,
        )
        print(f"Updated public route on {route_table_id}.")
        return

    try:
        ec2_client.create_route(
            RouteTableId=route_table_id,
            DestinationCidrBlock="0.0.0.0/0",
            GatewayId=internet_gateway_id,
        )
        print(f"Added public route to {route_table_id}.")
    except ClientError as error:
        if error.response["Error"]["Code"] != "RouteAlreadyExists":
            raise


def ensure_subnet(
    ec2_client,
    *,
    vpc_id: str,
    name: str,
    cidr_block: str,
    availability_zone: str,
    public: bool,
    tags: dict[str, str],
) -> str:
    existing = ec2_client.describe_subnets(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "tag:Name", "Values": [name]},
        ]
    )["Subnets"]
    subnet = _first_resource(existing)
    if subnet:
        subnet_id = subnet["SubnetId"]
    else:
        response = ec2_client.create_subnet(
            VpcId=vpc_id,
            CidrBlock=cidr_block,
            AvailabilityZone=availability_zone,
            TagSpecifications=[
                {"ResourceType": "subnet", "Tags": _with_name(tags, name)},
            ],
        )
        subnet_id = response["Subnet"]["SubnetId"]
        print(f"Created subnet {name} ({subnet_id}).")

    ec2_client.modify_subnet_attribute(
        SubnetId=subnet_id,
        MapPublicIpOnLaunch={"Value": public},
    )
    return subnet_id


def ensure_route_table_association(
    ec2_client,
    *,
    route_table_id: str,
    subnet_id: str,
) -> None:
    route_tables = ec2_client.describe_route_tables(
        Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
    )["RouteTables"]
    for route_table in route_tables:
        for association in route_table.get("Associations", []):
            if association.get("SubnetId") != subnet_id:
                continue
            if route_table["RouteTableId"] == route_table_id:
                return
            ec2_client.replace_route_table_association(
                AssociationId=association["RouteTableAssociationId"],
                RouteTableId=route_table_id,
            )
            print(f"Reassociated subnet {subnet_id} with route table {route_table_id}.")
            return

    ec2_client.associate_route_table(RouteTableId=route_table_id, SubnetId=subnet_id)
    print(f"Associated subnet {subnet_id} with route table {route_table_id}.")


def get_route_table_ids_for_subnets(
    ec2_client,
    *,
    vpc_id: str,
    subnet_ids: list[str],
) -> list[str]:
    route_tables = ec2_client.describe_route_tables(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )["RouteTables"]
    main_route_table_id = None
    subnet_to_route_table_id: dict[str, str] = {}

    for route_table in route_tables:
        route_table_id = route_table["RouteTableId"]
        for association in route_table.get("Associations", []):
            if association.get("Main"):
                main_route_table_id = route_table_id
            subnet_id = association.get("SubnetId")
            if subnet_id:
                subnet_to_route_table_id[subnet_id] = route_table_id

    if not main_route_table_id:
        raise ValueError(f"Could not find the main route table for VPC {vpc_id}.")

    route_table_ids = {
        subnet_to_route_table_id.get(subnet_id, main_route_table_id)
        for subnet_id in subnet_ids
    }
    return sorted(route_table_ids)


def ensure_s3_gateway_endpoint(
    *,
    region: str,
    vpc_id: str,
    subnet_ids: list[str],
    tags: dict[str, str],
) -> str:
    session = boto3.Session(region_name=region)
    ec2_client = session.client("ec2")
    route_table_ids = get_route_table_ids_for_subnets(
        ec2_client,
        vpc_id=vpc_id,
        subnet_ids=subnet_ids,
    )
    service_name = f"com.amazonaws.{region}.s3"
    existing = ec2_client.describe_vpc_endpoints(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "service-name", "Values": [service_name]},
            {"Name": "vpc-endpoint-type", "Values": ["Gateway"]},
        ]
    )["VpcEndpoints"]

    endpoint = _first_resource(
        [candidate for candidate in existing if candidate["State"] != "deleted"]
    )
    if endpoint:
        endpoint_id = endpoint["VpcEndpointId"]
        current_route_table_ids = set(endpoint.get("RouteTableIds", []))
        missing_route_table_ids = sorted(set(route_table_ids) - current_route_table_ids)
        if missing_route_table_ids:
            ec2_client.modify_vpc_endpoint(
                VpcEndpointId=endpoint_id,
                AddRouteTableIds=missing_route_table_ids,
            )
            print(
                f"Attached route tables {', '.join(missing_route_table_ids)} "
                f"to S3 endpoint {endpoint_id}."
            )
        else:
            print(f"S3 gateway endpoint {endpoint_id} already has the required route tables.")
        return endpoint_id

    response = ec2_client.create_vpc_endpoint(
        VpcEndpointType="Gateway",
        VpcId=vpc_id,
        ServiceName=service_name,
        RouteTableIds=route_table_ids,
        TagSpecifications=[
            {
                "ResourceType": "vpc-endpoint",
                "Tags": _with_name(tags, "proyecto-s3-gateway-endpoint"),
            }
        ],
    )
    endpoint_id = response["VpcEndpoint"]["VpcEndpointId"]
    print(
        f"Created S3 gateway endpoint {endpoint_id} for route tables "
        f"{', '.join(route_table_ids)}."
    )
    return endpoint_id


def create_vpc_stack(
    *,
    region: str,
    vpc_name: str,
    vpc_cidr_block: str,
    public_subnets: list[dict[str, str]],
    private_subnets: list[dict[str, str]],
    tags: dict[str, str],
) -> dict[str, list[str] | str]:
    session = boto3.Session(region_name=region)
    ec2_client = session.client("ec2")

    vpc_id = ensure_vpc(
        ec2_client,
        name=vpc_name,
        cidr_block=vpc_cidr_block,
        tags=tags,
    )
    internet_gateway_id = ensure_internet_gateway(
        ec2_client,
        vpc_id=vpc_id,
        name=f"{vpc_name}-igw",
        tags=tags,
    )
    public_route_table_id = ensure_route_table(
        ec2_client,
        vpc_id=vpc_id,
        name=f"{vpc_name}-public-rt",
        tags=tags,
    )
    ensure_public_route(
        ec2_client,
        route_table_id=public_route_table_id,
        internet_gateway_id=internet_gateway_id,
    )
    private_route_table_id = ensure_route_table(
        ec2_client,
        vpc_id=vpc_id,
        name=f"{vpc_name}-private-rt",
        tags=tags,
    )

    public_subnet_ids: list[str] = []
    for subnet in public_subnets:
        subnet_id = ensure_subnet(
            ec2_client,
            vpc_id=vpc_id,
            name=subnet["name"],
            cidr_block=subnet["cidr_block"],
            availability_zone=subnet["availability_zone"],
            public=True,
            tags=tags,
        )
        ensure_route_table_association(
            ec2_client,
            route_table_id=public_route_table_id,
            subnet_id=subnet_id,
        )
        public_subnet_ids.append(subnet_id)

    private_subnet_ids: list[str] = []
    for subnet in private_subnets:
        subnet_id = ensure_subnet(
            ec2_client,
            vpc_id=vpc_id,
            name=subnet["name"],
            cidr_block=subnet["cidr_block"],
            availability_zone=subnet["availability_zone"],
            public=False,
            tags=tags,
        )
        ensure_route_table_association(
            ec2_client,
            route_table_id=private_route_table_id,
            subnet_id=subnet_id,
        )
        private_subnet_ids.append(subnet_id)

    s3_gateway_endpoint_id = ensure_s3_gateway_endpoint(
        region=region,
        vpc_id=vpc_id,
        subnet_ids=private_subnet_ids,
        tags=tags,
    )

    return {
        "vpc_id": vpc_id,
        "internet_gateway_id": internet_gateway_id,
        "public_route_table_id": public_route_table_id,
        "private_route_table_id": private_route_table_id,
        "s3_gateway_endpoint_id": s3_gateway_endpoint_id,
        "public_subnet_ids": public_subnet_ids,
        "private_subnet_ids": private_subnet_ids,
    }
