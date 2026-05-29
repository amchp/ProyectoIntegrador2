#!/usr/bin/env python3
"""Run the full infra provisioning sequence in order."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from utils.common import read_env_file


INFRA_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]
    captures_artifact_uri: bool = False
    needs_artifact_uri: bool = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Proyecto Integrador infra setup in order.")
    parser.add_argument("--install-deps", action="store_true", help="Run pip install -r requirements.txt before provisioning.")
    parser.add_argument("--skip-pipeline", action="store_true", help="Do not start the Step Functions pipeline.")
    parser.add_argument("--skip-finbert", action="store_true", help="Stop before Elastic IP, EC2, artifact upload, service, and Lambda deploy.")
    parser.add_argument("--skip-lambda", action="store_true", help="Do not deploy the Kinesis-triggered Lambda consumer.")
    parser.add_argument("--artifact-uri", default="", help="Reuse this FinBERT model artifact URI and skip upload.")
    parser.add_argument("--force-upload", action="store_true", help="Upload the FinBERT artifact even when FINBERT_ARTIFACT_URI exists.")
    parser.add_argument("--snapshot-date", default="", help="Optional YYYY-MM-DD snapshot date for start_pipeline.py.")
    parser.add_argument("--pipeline-timeout-seconds", type=int, default=1800, help="Maximum time to wait for the Step Functions pipeline.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    return parser.parse_args()


def python_command(script_name: str, *args: str) -> list[str]:
    return [sys.executable, script_name, *args]


def build_steps(args: argparse.Namespace) -> list[Step]:
    steps: list[Step] = []
    if args.install_deps:
        steps.append(Step("Install infra dependencies", [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]))

    steps.extend(
        [
            Step("Create VPC", python_command("create_vpc.py")),
            Step("Create security groups", python_command("create_security_groups.py")),
            Step("Create raw S3 bucket", python_command("create_raw_bucket.py")),
            Step("Create features S3 bucket", python_command("create_features_bucket.py")),
            Step("Create RDS PostgreSQL", python_command("create_rds_postgres.py")),
            Step("Create Glue connection", python_command("create_glue_connection.py")),
            Step("Create FinBERT Kinesis stream", python_command("create_finbert_kinesis.py")),
            Step("Deploy Glue jobs", python_command("deploy_glue_jobs.py")),
            Step("Deploy Step Function", python_command("deploy_step_function.py")),
        ]
    )

    if not args.skip_pipeline:
        start_pipeline_command = python_command(
            "start_pipeline.py",
            "--wait-for-output",
            "--timeout-seconds",
            str(args.pipeline_timeout_seconds),
        )
        if args.snapshot_date:
            start_pipeline_command.extend(["--snapshot-date", args.snapshot_date])
        steps.append(Step("Start snapshot pipeline", start_pipeline_command))

    if args.skip_finbert:
        return steps

    steps.extend(
        [
            Step("Create FinBERT Elastic IP", python_command("create_finbert_elastic_ip.py")),
            Step("Create or start FinBERT EC2", python_command("create_finbert_ec2.py")),
            Step(
                "Upload FinBERT artifact",
                python_command(
                    "upload_finbert_artifact.py",
                    *(["--force-upload"] if args.force_upload else []),
                ),
                captures_artifact_uri=True,
            ),
            Step("Deploy FinBERT service", python_command("deploy_finbert_service.py"), needs_artifact_uri=True),
        ]
    )

    if not args.skip_lambda:
        steps.append(Step("Deploy FinBERT Lambda consumer", python_command("deploy_finbert_lambda_consumer.py")))

    return steps


def command_text(command: list[str]) -> str:
    return " ".join(command)


def subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    _, env_file_values = read_env_file()
    for name, value in env_file_values.items():
        if value:
            env[name] = value
    env["PYTHONUNBUFFERED"] = "1"
    return env


def existing_artifact_uri(args: argparse.Namespace) -> str:
    if args.artifact_uri:
        return args.artifact_uri
    _, env_file_values = read_env_file()
    return env_file_values.get("FINBERT_ARTIFACT_URI", "").strip()


def run_step(step: Step, *, artifact_uri: str) -> tuple[int, str]:
    command = list(step.command)
    if step.needs_artifact_uri:
        if not artifact_uri:
            raise RuntimeError("No artifact URI was captured before deploy_finbert_service.py.")
        command.extend(["--artifact-uri", artifact_uri])

    print(f"\n==> {step.name}")
    print(f"$ {command_text(command)}", flush=True)

    process = subprocess.Popen(
        command,
        cwd=INFRA_DIR,
        env=subprocess_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    captured_artifact_uri = artifact_uri
    assert process.stdout is not None
    for line in process.stdout:
        print(line, end="", flush=True)
        if step.captures_artifact_uri and line.startswith("artifact_uri="):
            captured_artifact_uri = line.split("=", 1)[1].strip()

    return process.wait(), captured_artifact_uri


def main() -> int:
    args = parse_args()
    steps = build_steps(args)
    artifact_uri = existing_artifact_uri(args)

    if args.dry_run:
        for step in steps:
            command = list(step.command)
            if step.needs_artifact_uri:
                command.extend(["--artifact-uri", artifact_uri or "<artifact_uri from upload_finbert_artifact.py>"])
            print(command_text(command))
        return 0

    for step in steps:
        try:
            return_code, artifact_uri = run_step(step, artifact_uri=artifact_uri)
        except RuntimeError as error:
            print(f"\nERROR: {error}", file=sys.stderr)
            return 1
        if return_code != 0:
            print(f"\nERROR: {step.name} failed with exit code {return_code}.", file=sys.stderr)
            return return_code

    print("\nAll infra commands completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
