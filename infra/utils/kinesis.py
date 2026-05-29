"""Kinesis helper functions shared by infra commands."""

from __future__ import annotations

import time

from botocore.exceptions import ClientError

from utils.common import ensure


def wait_for_active(kinesis_client, *, stream_name: str) -> dict:
    while True:
        description = kinesis_client.describe_stream_summary(StreamName=stream_name)[
            "StreamDescriptionSummary"
        ]
        if description["StreamStatus"] == "ACTIVE":
            return description
        print(f"Waiting for Kinesis stream to become ACTIVE: {description['StreamStatus']}")
        time.sleep(5)


def find_stream(kinesis_client, *, stream_name: str) -> dict | None:
    try:
        return kinesis_client.describe_stream_summary(StreamName=stream_name)[
            "StreamDescriptionSummary"
        ]
    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise


def ensure_stream(
    kinesis_client,
    *,
    stream_name: str,
    tags: dict[str, str],
) -> dict:
    def update(description: dict) -> dict:
        print(f"Reusing Kinesis stream: {stream_name} ({description['StreamStatus']})")
        return description

    def create() -> dict:
        kinesis_client.create_stream(
            StreamName=stream_name,
            StreamModeDetails={"StreamMode": "ON_DEMAND"},
        )
        print(f"Created Kinesis stream: {stream_name}")
        description = wait_for_active(kinesis_client, stream_name=stream_name)
        try:
            kinesis_client.add_tags_to_stream(StreamName=stream_name, Tags=tags)
        except ClientError as tag_error:
            print(f"Could not tag Kinesis stream: {tag_error.response['Error']['Code']}")
        return description

    def setup(description: dict) -> dict:
        if description["StreamStatus"] == "ACTIVE":
            return description
        return wait_for_active(kinesis_client, stream_name=stream_name)

    return ensure(
        lambda: find_stream(kinesis_client, stream_name=stream_name),
        create,
        update=update,
        setup=setup,
    )
