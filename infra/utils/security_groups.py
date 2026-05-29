"""Shared security group provisioning helpers."""

from __future__ import annotations

from botocore.exceptions import ClientError

from utils.aws import aws_client
from utils.common import ensure, serialize_tags


def _named_tags(name: str, tags: dict[str, str]) -> list[dict[str, str]]:
    return serialize_tags({"Name": name, **tags})


def _ignore_client_error(error: ClientError, *codes: str) -> bool:
    return error.response["Error"]["Code"] in codes


def _group_filter(group_name: str) -> dict[str, list[str]]:
    return {"Name": "group-name", "Values": [group_name]}


def _vpc_filter(vpc_id: str) -> dict[str, list[str]]:
    return {"Name": "vpc-id", "Values": [vpc_id]}


def _describe_security_group(ec2_client, *, group_id: str) -> dict:
    return ec2_client.describe_security_groups(GroupIds=[group_id])["SecurityGroups"][0]


def _port_range_matches(permission: dict, protocol: str, from_port: int, to_port: int) -> bool:
    return (
        permission.get("IpProtocol") == protocol
        and permission.get("FromPort") == from_port
        and permission.get("ToPort") == to_port
    )


def get_security_group_id(
    ec2_client,
    *,
    group_name: str,
    vpc_id: str,
) -> str | None:
    groups = ec2_client.describe_security_groups(
        Filters=[
            _group_filter(group_name),
            _vpc_filter(vpc_id),
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
    def check() -> dict | None:
        groups = ec2_client.describe_security_groups(
            Filters=[_group_filter(group_name), _vpc_filter(vpc_id)]
        )["SecurityGroups"]
        return groups[0] if groups else None

    def setup(group: dict) -> str:
        return group["GroupId"]

    def create() -> dict:
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
        return {"GroupId": group_id}

    return ensure(check, create, setup=setup)


def _ensure_ingress(
    ec2_client,
    *,
    group_id: str,
    permission: dict,
    exists,
    message: str,
) -> None:
    def check() -> dict | None:
        group = _describe_security_group(ec2_client, group_id=group_id)
        for existing_permission in group.get("IpPermissions", []):
            if exists(existing_permission):
                return existing_permission
        return None

    def update(existing_permission: dict) -> None:
        return None

    def create() -> dict:
        try:
            ec2_client.authorize_security_group_ingress(
                GroupId=group_id,
                IpPermissions=[permission],
            )
            print(message)
        except ClientError as error:
            if not _ignore_client_error(error, "InvalidPermission.Duplicate"):
                raise
        return permission

    ensure(check, create, update=update)


def _cidr_ingress_permission(
    *,
    protocol: str,
    from_port: int,
    to_port: int,
    cidr_ip: str,
    description: str,
) -> dict:
    return {
        "IpProtocol": protocol,
        "FromPort": from_port,
        "ToPort": to_port,
        "IpRanges": [{"CidrIp": cidr_ip, "Description": description}],
    }


def _security_group_ingress_permission(
    *,
    protocol: str,
    from_port: int | None = None,
    to_port: int | None = None,
    source_group_id: str,
    description: str,
) -> dict:
    permission = {
        "IpProtocol": protocol,
        "UserIdGroupPairs": [
            {
                "GroupId": source_group_id,
                "Description": description,
            }
        ],
    }
    if from_port is not None and to_port is not None:
        permission["FromPort"] = from_port
        permission["ToPort"] = to_port
    return permission


def _has_cidr_ingress(
    permission: dict,
    *,
    protocol: str,
    from_port: int,
    to_port: int,
    cidr_ip: str,
) -> bool:
    return _port_range_matches(permission, protocol, from_port, to_port) and any(
        ip_range.get("CidrIp") == cidr_ip
        for ip_range in permission.get("IpRanges", [])
    )


def _has_security_group_ingress(
    permission: dict,
    *,
    protocol: str,
    from_port: int | None = None,
    to_port: int | None = None,
    source_group_id: str,
) -> bool:
    if permission.get("IpProtocol") != protocol:
        return False
    if from_port is not None and not _port_range_matches(
        permission,
        protocol,
        from_port,
        to_port,
    ):
        return False
    return any(
        pair.get("GroupId") == source_group_id
        for pair in permission.get("UserIdGroupPairs", [])
    )


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
    permission = _cidr_ingress_permission(
        protocol=protocol,
        from_port=from_port,
        to_port=to_port,
        cidr_ip=cidr_ip,
        description=description,
    )
    _ensure_ingress(
        ec2_client,
        group_id=group_id,
        permission=permission,
        exists=lambda existing: _has_cidr_ingress(
            existing,
            protocol=protocol,
            from_port=from_port,
            to_port=to_port,
            cidr_ip=cidr_ip,
        ),
        message=f"Allowed {protocol}:{from_port}-{to_port} from {cidr_ip} on {group_id}.",
    )


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
    permission = _security_group_ingress_permission(
        protocol=protocol,
        from_port=from_port,
        to_port=to_port,
        source_group_id=source_group_id,
        description=description,
    )
    _ensure_ingress(
        ec2_client,
        group_id=group_id,
        permission=permission,
        exists=lambda existing: _has_security_group_ingress(
            existing,
            protocol=protocol,
            from_port=from_port,
            to_port=to_port,
            source_group_id=source_group_id,
        ),
        message=f"Allowed {protocol}:{from_port}-{to_port} from {source_group_id} on {group_id}.",
    )


def ensure_self_ingress_all_traffic(
    ec2_client,
    *,
    group_id: str,
    description: str,
) -> None:
    permission = _security_group_ingress_permission(
        protocol="-1",
        source_group_id=group_id,
        description=description,
    )
    _ensure_ingress(
        ec2_client,
        group_id=group_id,
        permission=permission,
        exists=lambda existing: _has_security_group_ingress(
            existing,
            protocol="-1",
            source_group_id=group_id,
        ),
        message=f"Allowed all traffic from {group_id} on itself.",
    )


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
    ec2_client = aws_client("ec2", region=region)

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
