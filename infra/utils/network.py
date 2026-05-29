"""Shared VPC and subnet provisioning helpers."""

from __future__ import annotations

from botocore.exceptions import ClientError

from utils.aws import aws_client
from utils.common import ensure, serialize_tags


PUBLIC_ROUTE_CIDR = "0.0.0.0/0"
S3_ENDPOINT_NAME = "proyecto-s3-gateway-endpoint"


def ensure_vpc(
    ec2_client,
    *,
    name: str,
    cidr_block: str,
    tags: dict[str, str],
) -> str:
    def setup(vpc: dict) -> str:
        vpc_id = vpc["VpcId"]
        ec2_client.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
        ec2_client.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
        return vpc_id

    def create() -> dict:
        response = ec2_client.create_vpc(
            CidrBlock=cidr_block,
            TagSpecifications=[
                {"ResourceType": "vpc", "Tags": serialize_tags({"Name": name, **tags})}
            ],
        )
        vpc = response["Vpc"]
        print(f"Created VPC {name} ({vpc['VpcId']}).")
        return vpc

    return ensure(
        lambda: (
            ec2_client.describe_vpcs(
                Filters=[{"Name": "tag:Name", "Values": [name]}]
            )["Vpcs"]
            or [None]
        )[0],
        create,
        setup=setup,
    )


def ensure_internet_gateway(
    ec2_client,
    *,
    vpc_id: str,
    name: str,
    tags: dict[str, str],
) -> str:
    def attach(internet_gateway: dict) -> str:
        internet_gateway_id = internet_gateway["InternetGatewayId"]
        attachments = internet_gateway.get("Attachments", [])
        if any(attachment["VpcId"] == vpc_id for attachment in attachments):
            return internet_gateway_id
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

    def create() -> dict:
        response = ec2_client.create_internet_gateway(
            TagSpecifications=[
                {
                    "ResourceType": "internet-gateway",
                    "Tags": serialize_tags({"Name": name, **tags}),
                }
            ]
        )
        internet_gateway = response["InternetGateway"]
        print(f"Created internet gateway {name} ({internet_gateway['InternetGatewayId']}).")
        return internet_gateway

    return ensure(
        lambda: (
            ec2_client.describe_internet_gateways(
                Filters=[{"Name": "tag:Name", "Values": [name]}]
            )["InternetGateways"]
            or [None]
        )[0],
        create,
        setup=attach,
    )


def ensure_route_table(
    ec2_client,
    *,
    vpc_id: str,
    name: str,
    tags: dict[str, str],
) -> str:
    def setup(route_table: dict) -> str:
        return route_table["RouteTableId"]

    def create() -> dict:
        response = ec2_client.create_route_table(
            VpcId=vpc_id,
            TagSpecifications=[
                {
                    "ResourceType": "route-table",
                    "Tags": serialize_tags({"Name": name, **tags}),
                }
            ],
        )
        route_table = response["RouteTable"]
        print(f"Created route table {name} ({route_table['RouteTableId']}).")
        return route_table

    return ensure(
        lambda: (
            ec2_client.describe_route_tables(
                Filters=[
                    {"Name": "vpc-id", "Values": [vpc_id]},
                    {"Name": "tag:Name", "Values": [name]},
                ]
            )["RouteTables"]
            or [None]
        )[0],
        create,
        setup=setup,
    )


def ensure_public_route(
    ec2_client,
    *,
    route_table_id: str,
    internet_gateway_id: str,
) -> None:
    def check() -> dict | None:
        route_table = ec2_client.describe_route_tables(RouteTableIds=[route_table_id])[
            "RouteTables"
        ][0]
        for route in route_table.get("Routes", []):
            if route.get("DestinationCidrBlock") == PUBLIC_ROUTE_CIDR:
                return route
        return None

    def update(route: dict) -> None:
        if route.get("GatewayId") == internet_gateway_id:
            return
        ec2_client.replace_route(
            RouteTableId=route_table_id,
            DestinationCidrBlock=PUBLIC_ROUTE_CIDR,
            GatewayId=internet_gateway_id,
        )
        print(f"Updated public route on {route_table_id}.")

    def create() -> dict:
        try:
            ec2_client.create_route(
                RouteTableId=route_table_id,
                DestinationCidrBlock=PUBLIC_ROUTE_CIDR,
                GatewayId=internet_gateway_id,
            )
            print(f"Added public route to {route_table_id}.")
        except ClientError as error:
            if error.response["Error"]["Code"] != "RouteAlreadyExists":
                raise
        return {"DestinationCidrBlock": PUBLIC_ROUTE_CIDR, "GatewayId": internet_gateway_id}

    ensure(check, create, update=update)


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
    def setup(subnet: dict) -> str:
        ec2_client.modify_subnet_attribute(
            SubnetId=subnet["SubnetId"],
            MapPublicIpOnLaunch={"Value": public},
        )
        return subnet["SubnetId"]

    def create() -> dict:
        response = ec2_client.create_subnet(
            VpcId=vpc_id,
            CidrBlock=cidr_block,
            AvailabilityZone=availability_zone,
            TagSpecifications=[
                {
                    "ResourceType": "subnet",
                    "Tags": serialize_tags({"Name": name, **tags}),
                }
            ],
        )
        subnet = response["Subnet"]
        print(f"Created subnet {name} ({subnet['SubnetId']}).")
        return subnet

    return ensure(
        lambda: (
            ec2_client.describe_subnets(
                Filters=[
                    {"Name": "vpc-id", "Values": [vpc_id]},
                    {"Name": "tag:Name", "Values": [name]},
                ]
            )["Subnets"]
            or [None]
        )[0],
        create,
        setup=setup,
    )


def ensure_route_table_association(
    ec2_client,
    *,
    route_table_id: str,
    subnet_id: str,
) -> None:
    def check() -> dict | None:
        route_tables = ec2_client.describe_route_tables(
            Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
        )["RouteTables"]
        for route_table in route_tables:
            for association in route_table.get("Associations", []):
                if association.get("SubnetId") == subnet_id:
                    return {
                        "association_id": association["RouteTableAssociationId"],
                        "route_table_id": route_table["RouteTableId"],
                    }
        return None

    def update(association: dict) -> None:
        if association["route_table_id"] == route_table_id:
            return
        ec2_client.replace_route_table_association(
            AssociationId=association["association_id"],
            RouteTableId=route_table_id,
        )
        print(f"Reassociated subnet {subnet_id} with route table {route_table_id}.")

    def create() -> dict:
        response = ec2_client.associate_route_table(
            RouteTableId=route_table_id,
            SubnetId=subnet_id,
        )
        print(f"Associated subnet {subnet_id} with route table {route_table_id}.")
        return {
            "association_id": response["AssociationId"],
            "route_table_id": route_table_id,
        }

    ensure(check, create, update=update)


def ensure_s3_gateway_endpoint(
    ec2_client,
    *,
    region: str,
    vpc_id: str,
    subnet_ids: list[str],
    tags: dict[str, str],
) -> str:
    def route_table_ids_for_subnets() -> list[str]:
        route_tables = ec2_client.describe_route_tables(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
        )["RouteTables"]
        associations = [
            (route_table["RouteTableId"], association)
            for route_table in route_tables
            for association in route_table.get("Associations", [])
        ]
        main_route_table_id = next(
            (
                route_table_id
                for route_table_id, association in associations
                if association.get("Main")
            ),
            None,
        )
        if not main_route_table_id:
            raise ValueError(f"Could not find the main route table for VPC {vpc_id}.")

        subnet_to_route_table_id = {
            association["SubnetId"]: route_table_id
            for route_table_id, association in associations
            if association.get("SubnetId")
        }
        return sorted(
            {
                subnet_to_route_table_id.get(subnet_id, main_route_table_id)
                for subnet_id in subnet_ids
            }
        )

    route_table_ids = route_table_ids_for_subnets()
    service_name = f"com.amazonaws.{region}.s3"

    def check() -> dict | None:
        existing = ec2_client.describe_vpc_endpoints(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc_id]},
                {"Name": "service-name", "Values": [service_name]},
                {"Name": "vpc-endpoint-type", "Values": ["Gateway"]},
            ]
        )["VpcEndpoints"]
        endpoints = [candidate for candidate in existing if candidate["State"] != "deleted"]
        return (endpoints or [None])[0]

    def attach_route_tables(endpoint: dict) -> str:
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
        elif not endpoint.get("_created"):
            print(f"S3 gateway endpoint {endpoint_id} already has the required route tables.")
        return endpoint_id

    def create() -> dict:
        response = ec2_client.create_vpc_endpoint(
            VpcEndpointType="Gateway",
            VpcId=vpc_id,
            ServiceName=service_name,
            RouteTableIds=route_table_ids,
            TagSpecifications=[
                {
                    "ResourceType": "vpc-endpoint",
                    "Tags": serialize_tags({"Name": S3_ENDPOINT_NAME, **tags}),
                }
            ],
        )
        endpoint = response["VpcEndpoint"]
        endpoint["RouteTableIds"] = route_table_ids
        print(
            f"Created S3 gateway endpoint {endpoint['VpcEndpointId']} for route tables "
            f"{', '.join(route_table_ids)}."
        )
        return endpoint

    return ensure(check, create, setup=attach_route_tables)


def _ensure_subnets(
    ec2_client,
    *,
    vpc_id: str,
    subnets: list[dict[str, str]],
    route_table_id: str,
    public: bool,
    tags: dict[str, str],
) -> list[str]:
    subnet_ids: list[str] = []
    for subnet in subnets:
        subnet_id = ensure_subnet(
            ec2_client,
            vpc_id=vpc_id,
            name=subnet["name"],
            cidr_block=subnet["cidr_block"],
            availability_zone=subnet["availability_zone"],
            public=public,
            tags=tags,
        )
        ensure_route_table_association(
            ec2_client,
            route_table_id=route_table_id,
            subnet_id=subnet_id,
        )
        subnet_ids.append(subnet_id)
    return subnet_ids


def create_vpc_stack(
    *,
    region: str,
    vpc_name: str,
    vpc_cidr_block: str,
    public_subnets: list[dict[str, str]],
    private_subnets: list[dict[str, str]],
    tags: dict[str, str],
) -> dict[str, list[str] | str]:
    ec2_client = aws_client("ec2", region=region)

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

    public_subnet_ids = _ensure_subnets(
        ec2_client,
        vpc_id=vpc_id,
        subnets=public_subnets,
        route_table_id=public_route_table_id,
        public=True,
        tags=tags,
    )
    private_subnet_ids = _ensure_subnets(
        ec2_client,
        vpc_id=vpc_id,
        subnets=private_subnets,
        route_table_id=private_route_table_id,
        public=False,
        tags=tags,
    )

    s3_gateway_endpoint_id = ensure_s3_gateway_endpoint(
        ec2_client,
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
