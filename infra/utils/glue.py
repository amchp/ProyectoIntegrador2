"""Glue helper functions shared by infra commands."""

from __future__ import annotations

from botocore.exceptions import ClientError

from utils.aws import aws_clients
from utils.common import ensure
from utils.rds import get_rds_endpoint
from utils.security_groups import get_security_group_id


def s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


def get_subnet_availability_zone(ec2_client, subnet_id: str) -> str:
    response = ec2_client.describe_subnets(SubnetIds=[subnet_id])
    return response["Subnets"][0]["AvailabilityZone"]


def ensure_connection(glue_client, *, connection_name: str, connection_input: dict) -> None:
    def check() -> dict | None:
        try:
            return glue_client.get_connection(Name=connection_name)["Connection"]
        except ClientError as error:
            if error.response["Error"]["Code"] == "EntityNotFoundException":
                return None
            raise

    def update(connection: dict) -> dict:
        glue_client.update_connection(
            Name=connection_name,
            ConnectionInput=connection_input,
        )
        print(f"Updated Glue connection: {connection_name}")
        return connection

    def create() -> dict:
        glue_client.create_connection(ConnectionInput=connection_input)
        print(f"Created Glue connection: {connection_name}")
        return connection_input

    ensure(check, create, update=update)


def base_default_arguments(
    *,
    deploy_bucket: str,
    deploy_prefix: str,
    additional_python_modules: str,
    extra_py_files_uri: str,
) -> dict[str, str]:
    arguments = {
        "--job-language": "python",
        "--enable-metrics": "true",
        "--enable-continuous-cloudwatch-log": "true",
        "--TempDir": s3_uri(deploy_bucket, f"{deploy_prefix}/temp/"),
        "--extra-py-files": extra_py_files_uri,
    }
    if additional_python_modules:
        arguments["--additional-python-modules"] = additional_python_modules
    return arguments


def job_definition(
    *,
    role_arn: str,
    script_location: str,
    default_arguments: dict[str, str],
    connections: list[str],
    glue_version: str,
    worker_type: str,
    number_of_workers: int,
    timeout_minutes: int,
    max_retries: int,
    max_concurrent_runs: int,
) -> dict:
    definition = {
        "Role": role_arn,
        "ExecutionProperty": {"MaxConcurrentRuns": max_concurrent_runs},
        "Command": {
            "Name": "glueetl",
            "ScriptLocation": script_location,
            "PythonVersion": "3",
        },
        "DefaultArguments": default_arguments,
        "GlueVersion": glue_version,
        "WorkerType": worker_type,
        "NumberOfWorkers": number_of_workers,
        "Timeout": timeout_minutes,
        "MaxRetries": max_retries,
    }
    if connections:
        definition["Connections"] = {"Connections": connections}
    return definition


def create_or_update_job(
    glue_client,
    *,
    job_name: str,
    definition: dict,
) -> None:
    def check() -> dict | None:
        try:
            return glue_client.get_job(JobName=job_name)["Job"]
        except ClientError as error:
            if error.response["Error"]["Code"] == "EntityNotFoundException":
                return None
            raise

    def update(job: dict) -> dict:
        glue_client.update_job(JobName=job_name, JobUpdate=definition)
        print(f"Updated Glue job: {job_name}")
        return job

    def create() -> dict:
        response = glue_client.create_job(Name=job_name, **definition)
        print(f"Created Glue job: {job_name}")
        return response

    ensure(check, create, update=update)


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
    jdbc_driver_class_name: str,
) -> None:
    ec2_client, rds_client, glue_client = aws_clients(region, "ec2", "rds", "glue")
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
            "JDBC_DRIVER_CLASS_NAME": jdbc_driver_class_name,
        },
        "PhysicalConnectionRequirements": {
            "AvailabilityZone": get_subnet_availability_zone(ec2_client, subnet_id),
            "SecurityGroupIdList": [security_group_id],
            "SubnetId": subnet_id,
        },
    }

    ensure_connection(
        glue_client,
        connection_name=connection_name,
        connection_input=connection_input,
    )
