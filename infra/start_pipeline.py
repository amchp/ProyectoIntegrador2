#!/usr/bin/env python3
"""Start the financial sentiment Step Functions pipeline."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError
from deploy_step_function import STATE_MACHINE_NAME, get_state_machine_arn
from utils.common import resolve_region


AWS_REGION = "us-east-1"
LOCAL_TIMEZONE = "America/Bogota"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-date", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    region = resolve_region(AWS_REGION)
    now = datetime.now(ZoneInfo(LOCAL_TIMEZONE))
    snapshot_date = args.snapshot_date or now.date().isoformat()
    run_id = now.strftime("%Y%m%d-%H%M%S")
    input_payload = {"snapshot_date": snapshot_date, "run_id": run_id}

    client = boto3.client("stepfunctions", region_name=region)
    state_machine_arn = get_state_machine_arn(client, STATE_MACHINE_NAME)
    if not state_machine_arn:
        raise SystemExit(f"Step Function not found: {STATE_MACHINE_NAME}")
    response = client.start_execution(
        stateMachineArn=state_machine_arn,
        name=f"financial-sentiment-{run_id}",
        input=json.dumps(input_payload),
    )
    print(f"Started execution: {response['executionArn']}")
    print(json.dumps(input_payload, indent=2))


if __name__ == "__main__":
    try:
        main()
    except ClientError as error:
        raise SystemExit(f"AWS error: {error}")
