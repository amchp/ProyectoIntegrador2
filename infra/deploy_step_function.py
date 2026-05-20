#!/usr/bin/env python3
"""Create or update the financial sentiment Step Functions state machine."""

from __future__ import annotations

import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from utils.common import require_env, resolve_region


AWS_REGION = "us-east-1"
STATE_MACHINE_NAME = "financial-sentiment-raw-curated-features"
STATE_MACHINE_TYPE = "STANDARD"
DEFINITION_PATH = (
    Path(__file__).resolve().parent
    / "step_functions"
    / "glue_financial_sentiment_raw_curated_features.json"
)

def load_definition(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return json.dumps(payload)


def get_state_machine_arn(stepfunctions_client, name: str) -> str | None:
    paginator = stepfunctions_client.get_paginator("list_state_machines")
    for page in paginator.paginate():
        for state_machine in page["stateMachines"]:
            if state_machine["name"] == name:
                return state_machine["stateMachineArn"]
    return None


def deploy_state_machine(
    *,
    region: str,
    state_machine_name: str,
    role_arn: str,
    definition_path: Path,
    state_machine_type: str,
) -> None:
    stepfunctions_client = boto3.client("stepfunctions", region_name=region)
    definition = load_definition(definition_path)
    existing_arn = get_state_machine_arn(stepfunctions_client, state_machine_name)

    if existing_arn:
        stepfunctions_client.update_state_machine(
            stateMachineArn=existing_arn,
            definition=definition,
            roleArn=role_arn,
        )
        print(f"Updated Step Function: {existing_arn}")
        return

    response = stepfunctions_client.create_state_machine(
        name=state_machine_name,
        definition=definition,
        roleArn=role_arn,
        type=state_machine_type,
    )
    print(f"Created Step Function: {response['stateMachineArn']}")


if __name__ == "__main__":
    try:
        deploy_state_machine(
            region=resolve_region(AWS_REGION),
            state_machine_name=STATE_MACHINE_NAME,
            role_arn=require_env(
                "STATE_MACHINE_ROLE_ARN",
                placeholder_prefixes=("arn:aws:iam::123456789012:",),
            ),
            definition_path=DEFINITION_PATH,
            state_machine_type=STATE_MACHINE_TYPE,
        )
    except ClientError as error:
        raise SystemExit(f"AWS error: {error}")
