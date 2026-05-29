"""RDS helper functions shared by infra commands."""

from __future__ import annotations

from botocore.exceptions import ClientError

from utils.aws import aws_clients
from utils.common import ensure, serialize_tags
from utils.security_groups import get_security_group_id


def ensure_subnet_group(
    rds_client,
    *,
    subnet_group_name: str,
    subnet_ids: list[str],
    tags: dict[str, str],
) -> None:
    if len(subnet_ids) < 2:
        raise ValueError("Provide at least two subnet IDs for the RDS subnet group.")

    def check() -> dict | None:
        try:
            return rds_client.describe_db_subnet_groups(
                DBSubnetGroupName=subnet_group_name
            )["DBSubnetGroups"][0]
        except ClientError as error:
            if error.response["Error"]["Code"] == "DBSubnetGroupNotFoundFault":
                return None
            raise

    def update(subnet_group: dict) -> dict:
        print(f"Subnet group {subnet_group_name} already exists. Reusing it.")
        return subnet_group

    def create() -> dict:
        rds_client.create_db_subnet_group(
            DBSubnetGroupName=subnet_group_name,
            DBSubnetGroupDescription="Subnets for ProyectoDeGrado PostgreSQL",
            SubnetIds=subnet_ids,
            Tags=serialize_tags(tags),
        )
        print(f"Created subnet group {subnet_group_name}.")
        return {"DBSubnetGroupName": subnet_group_name}

    ensure(check, create, update=update)


def get_existing_instance(rds_client, identifier: str) -> dict | None:
    try:
        response = rds_client.describe_db_instances(DBInstanceIdentifier=identifier)
    except ClientError as error:
        if error.response["Error"]["Code"] in {"DBInstanceNotFound", "DBInstanceNotFoundFault"}:
            return None
        raise
    return response["DBInstances"][0]


def get_rds_endpoint(rds_client, db_instance_identifier: str) -> tuple[str, int]:
    response = rds_client.describe_db_instances(
        DBInstanceIdentifier=db_instance_identifier,
    )
    instance = response["DBInstances"][0]
    if instance["DBInstanceStatus"] != "available":
        raise ValueError(
            f"RDS instance {db_instance_identifier} is {instance['DBInstanceStatus']}. "
            "Wait until it is available before creating dependent resources."
        )

    endpoint = instance.get("Endpoint", {})
    address = endpoint.get("Address")
    port = endpoint.get("Port")
    if not address or not port:
        raise ValueError(f"RDS instance {db_instance_identifier} does not have an endpoint yet.")
    return address, port


def print_instance_summary(instance: dict) -> None:
    endpoint = instance.get("Endpoint", {})
    address = endpoint.get("Address", "pending")
    port = endpoint.get("Port", "pending")
    print(f"RDS ready: {instance['DBInstanceIdentifier']}")
    print(f"Status: {instance['DBInstanceStatus']}")
    print(f"Endpoint: {address}:{port}")


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

    ec2_client, rds_client = aws_clients(region, "ec2", "rds")
    subnet_group_name = f"{db_instance_identifier}-subnet-group"

    def check() -> dict | None:
        return get_existing_instance(rds_client, db_instance_identifier)

    def update(instance: dict) -> dict:
        print(f"RDS instance {db_instance_identifier} already exists. Reusing it.")
        print_instance_summary(instance)
        return instance

    def create() -> dict:
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
        return {"DBInstanceIdentifier": db_instance_identifier}

    def setup(instance: dict) -> None:
        if instance.get("DBInstanceStatus"):
            return
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

    ensure(check, create, update=update, setup=setup)
