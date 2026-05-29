"""Lambda helper functions shared by infra commands."""

from __future__ import annotations

import time

from botocore.exceptions import ClientError

from utils.common import ensure


def wait_for_function_ready(lambda_client, *, function_name: str) -> None:
    while True:
        config = lambda_client.get_function_configuration(FunctionName=function_name)
        state = config.get("State")
        last_update = config.get("LastUpdateStatus")
        if state == "Active" and last_update in {None, "Successful"}:
            return
        print(f"Waiting for Lambda function readiness: State={state} LastUpdateStatus={last_update}")
        time.sleep(5)


def ensure_event_source_mapping(
    lambda_client,
    *,
    stream_arn: str,
    function_name: str,
    batch_size: int = 1,
) -> None:
    def check() -> dict | None:
        mappings = lambda_client.list_event_source_mappings(
            EventSourceArn=stream_arn,
            FunctionName=function_name,
        )["EventSourceMappings"]
        return mappings[0] if mappings else None

    def update(mapping: dict) -> dict:
        lambda_client.update_event_source_mapping(
            UUID=mapping["UUID"],
            BatchSize=batch_size,
            Enabled=True,
        )
        print(f"Updated Kinesis event source mapping: {mapping['UUID']}")
        return mapping

    def create() -> dict:
        response = lambda_client.create_event_source_mapping(
            EventSourceArn=stream_arn,
            FunctionName=function_name,
            StartingPosition="LATEST",
            BatchSize=batch_size,
            Enabled=True,
        )
        print(f"Created Kinesis event source mapping: {response['UUID']}")
        return response

    ensure(check, create, update=update)


def ensure_zip_function(
    lambda_client,
    *,
    function_name: str,
    zip_bytes: bytes,
    role: str,
    runtime: str,
    handler: str,
    timeout_seconds: int,
    memory_mb: int,
    environment: dict,
    vpc_config: dict,
    tags: dict[str, str],
) -> str:
    def check() -> dict | None:
        try:
            return lambda_client.get_function(FunctionName=function_name)["Configuration"]
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise

    def update(configuration: dict) -> str:
        function_arn = configuration["FunctionArn"]
        lambda_client.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
        wait_for_function_ready(lambda_client, function_name=function_name)
        lambda_client.update_function_configuration(
            FunctionName=function_name,
            Role=role,
            Runtime=runtime,
            Handler=handler,
            Timeout=timeout_seconds,
            MemorySize=memory_mb,
            Environment=environment,
            VpcConfig=vpc_config,
        )
        wait_for_function_ready(lambda_client, function_name=function_name)
        print(f"Updated Lambda function: {function_name}")
        return function_arn

    def create() -> str:
        response = lambda_client.create_function(
            FunctionName=function_name,
            Runtime=runtime,
            Role=role,
            Handler=handler,
            Code={"ZipFile": zip_bytes},
            Timeout=timeout_seconds,
            MemorySize=memory_mb,
            Environment=environment,
            VpcConfig=vpc_config,
            Tags=tags,
        )
        wait_for_function_ready(lambda_client, function_name=function_name)
        print(f"Created Lambda function: {function_name}")
        return response["FunctionArn"]

    return ensure(check, create, update=update)
