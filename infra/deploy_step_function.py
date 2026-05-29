#!/usr/bin/env python3
"""Create or update the financial sentiment Step Functions state machine."""

from __future__ import annotations

from pathlib import Path

from botocore.exceptions import ClientError
from utils.aws import aws_client, lab_role_arn
from utils.common import load_json_document, persist_env_values, resolve_region
from utils.stepfunctions import ensure_state_machine


AWS_REGION = "us-east-1"
STATE_MACHINE_NAME = "financial-sentiment-raw-curated-features"
STATE_MACHINE_TYPE = "STANDARD"
DEFINITION_PATH = (
    Path(__file__).resolve().parent
    / "step_functions"
    / "glue_financial_sentiment_raw_curated_features.json"
)

def deploy_state_machine(
    *,
    region: str,
    state_machine_name: str,
    role_arn: str,
    definition_path: Path,
    state_machine_type: str,
) -> str:
    stepfunctions_client = aws_client("stepfunctions", region=region)
    definition = load_json_document(definition_path)
    return ensure_state_machine(
        stepfunctions_client,
        state_machine_name=state_machine_name,
        role_arn=role_arn,
        definition=definition,
        state_machine_type=state_machine_type,
    )


if __name__ == "__main__":
    try:
        region = resolve_region(AWS_REGION)
        iam_client = aws_client("iam", region=region)
        state_machine_arn = deploy_state_machine(
            region=region,
            state_machine_name=STATE_MACHINE_NAME,
            role_arn=lab_role_arn(iam_client),
            definition_path=DEFINITION_PATH,
            state_machine_type=STATE_MACHINE_TYPE,
        )
        persist_env_values(
            {
                "AWS_REGION": region,
                "STATE_MACHINE_ARN": state_machine_arn,
            }
        )
    except ClientError as error:
        raise SystemExit(f"AWS error: {error}")
