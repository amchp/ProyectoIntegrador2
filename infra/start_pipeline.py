#!/usr/bin/env python3
"""Start the financial sentiment Step Functions pipeline."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from utils.aws import aws_client
from utils.common import load_local_env, resolve_region


AWS_REGION = "us-east-1"
STATE_MACHINE_NAME = "financial-sentiment-raw-curated-features"
LOCAL_TIMEZONE = "America/Bogota"
FEATURES_BUCKET = "proyecto-integrador-2-features-amce"
FEATURES_PREFIX = "features/financial_sentiment"
TERMINAL_EXECUTION_STATUSES = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}
STATE_MACHINE_LOOKUP_TIMEOUT_SECONDS = 120


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-date", default="")
    parser.add_argument("--wait", action="store_true", help="Wait until the Step Functions execution finishes.")
    parser.add_argument("--wait-for-output", action="store_true", help="Wait until the final feature report exists in S3.")
    parser.add_argument("--poll-seconds", type=int, default=30, help="Seconds between execution status checks.")
    parser.add_argument("--timeout-seconds", type=int, default=1800, help="Maximum wait time when --wait is used.")
    return parser.parse_args()


def find_state_machine_arn(stepfunctions_client, *, state_machine_name: str) -> str | None:
    paginator = stepfunctions_client.get_paginator("list_state_machines")
    for page in paginator.paginate():
        for state_machine in page["stateMachines"]:
            if state_machine["name"] == state_machine_name:
                return state_machine["stateMachineArn"]
    return None


def configured_state_machine_arn() -> str:
    import os

    load_local_env()
    return os.getenv("STATE_MACHINE_ARN", "").strip()


def wait_for_state_machine_arn(
    stepfunctions_client,
    *,
    state_machine_name: str,
    poll_seconds: int,
    timeout_seconds: int = STATE_MACHINE_LOOKUP_TIMEOUT_SECONDS,
) -> str:
    configured_arn = configured_state_machine_arn()
    if configured_arn:
        try:
            stepfunctions_client.describe_state_machine(stateMachineArn=configured_arn)
            return configured_arn
        except Exception as error:
            if error.__class__.__name__ != "ClientError":
                raise
            print(f"Configured STATE_MACHINE_ARN is not ready yet: {configured_arn}")

    started_at = time.monotonic()
    while True:
        state_machine_arn = find_state_machine_arn(
            stepfunctions_client,
            state_machine_name=state_machine_name,
        )
        if state_machine_arn:
            return state_machine_arn

        if time.monotonic() - started_at >= timeout_seconds:
            raise SystemExit(
                f"Step Function not found in this AWS account/region after {timeout_seconds} seconds: "
                f"{state_machine_name}"
            )
        print(f"Waiting for Step Function to be visible: {state_machine_name}")
        time.sleep(poll_seconds)


def wait_for_execution(stepfunctions_client, *, execution_arn: str, poll_seconds: int, timeout_seconds: int) -> None:
    started_at = time.monotonic()
    while True:
        execution = stepfunctions_client.describe_execution(executionArn=execution_arn)
        status = execution["status"]
        print(f"Execution status: {status}")
        if status in TERMINAL_EXECUTION_STATUSES:
            if status != "SUCCEEDED":
                raise SystemExit(f"Step Function execution finished with status: {status}")
            return
        if time.monotonic() - started_at >= timeout_seconds:
            raise SystemExit(f"Timed out waiting for Step Function execution after {timeout_seconds} seconds.")
        time.sleep(poll_seconds)


def final_feature_report_key(*, features_prefix: str, snapshot_date: str) -> str:
    partition_segment = f"snapshot_date={quote(snapshot_date, safe='')}"
    return f"{features_prefix.strip('/')}/reports/{partition_segment}/split_distribution.csv"


def s3_object_exists(s3_client, *, bucket: str, key: str) -> bool:
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as error:
        error_response = getattr(error, "response", {})
        error_code = error_response.get("Error", {}).get("Code")
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def wait_for_feature_output(
    *,
    stepfunctions_client,
    s3_client,
    execution_arn: str,
    bucket: str,
    key: str,
    poll_seconds: int,
    timeout_seconds: int,
) -> None:
    started_at = time.monotonic()
    output_uri = f"s3://{bucket}/{key}"
    while True:
        if s3_object_exists(s3_client, bucket=bucket, key=key):
            print(f"Feature output ready: {output_uri}")
            return

        execution = stepfunctions_client.describe_execution(executionArn=execution_arn)
        status = execution["status"]
        print(f"Waiting for feature output: {output_uri} (execution status: {status})")
        if status in TERMINAL_EXECUTION_STATUSES and status != "SUCCEEDED":
            raise SystemExit(f"Step Function execution finished with status: {status}")
        if time.monotonic() - started_at >= timeout_seconds:
            raise SystemExit(f"Timed out waiting for feature output after {timeout_seconds} seconds: {output_uri}")
        time.sleep(poll_seconds)


def main() -> None:
    args = parse_args()
    region = resolve_region(AWS_REGION)
    now = datetime.now(ZoneInfo(LOCAL_TIMEZONE))
    snapshot_date = args.snapshot_date or now.date().isoformat()
    run_id = now.strftime("%Y%m%d-%H%M%S")
    input_payload = {"snapshot_date": snapshot_date, "run_id": run_id}

    client = aws_client("stepfunctions", region=region)
    state_machine_arn = wait_for_state_machine_arn(
        client,
        state_machine_name=STATE_MACHINE_NAME,
        poll_seconds=args.poll_seconds,
    )
    response = client.start_execution(
        stateMachineArn=state_machine_arn,
        name=f"financial-sentiment-{run_id}",
        input=json.dumps(input_payload),
    )
    print(f"Started execution: {response['executionArn']}")
    print(json.dumps(input_payload, indent=2))
    if args.wait:
        wait_for_execution(
            client,
            execution_arn=response["executionArn"],
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
        )
    if args.wait_for_output:
        s3_client = aws_client("s3", region=region)
        wait_for_feature_output(
            stepfunctions_client=client,
            s3_client=s3_client,
            execution_arn=response["executionArn"],
            bucket=FEATURES_BUCKET,
            key=final_feature_report_key(features_prefix=FEATURES_PREFIX, snapshot_date=snapshot_date),
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if error.__class__.__name__ == "ClientError":
            raise SystemExit(f"AWS error: {error}") from error
        raise
