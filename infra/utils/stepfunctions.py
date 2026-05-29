"""Step Functions helper functions shared by infra commands."""

from __future__ import annotations

from pathlib import Path

from utils.common import ensure


def ensure_state_machine(
    stepfunctions_client,
    *,
    state_machine_name: str,
    role_arn: str,
    definition: str,
    state_machine_type: str,
) -> str:
    def check() -> str | None:
        paginator = stepfunctions_client.get_paginator("list_state_machines")
        for page in paginator.paginate():
            for state_machine in page["stateMachines"]:
                if state_machine["name"] == state_machine_name:
                    return state_machine["stateMachineArn"]
        return None

    def update(state_machine_arn: str) -> str:
        stepfunctions_client.update_state_machine(
            stateMachineArn=state_machine_arn,
            definition=definition,
            roleArn=role_arn,
        )
        print(f"Updated Step Function: {state_machine_arn}")
        return state_machine_arn

    def create() -> str:
        response = stepfunctions_client.create_state_machine(
            name=state_machine_name,
            definition=definition,
            roleArn=role_arn,
            type=state_machine_type,
        )
        state_machine_arn = response["stateMachineArn"]
        print(f"Created Step Function: {state_machine_arn}")
        return state_machine_arn

    return ensure(
        check,
        create,
        update=update,
    )
