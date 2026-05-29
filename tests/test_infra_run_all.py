from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
INFRA_DIR = ROOT / "infra"
sys.path.insert(0, str(INFRA_DIR))

import run_all


def script_name(step: run_all.Step) -> str | None:
    return next((token for token in step.command if token.endswith(".py")), None)


class InfraRunAllTests(unittest.TestCase):
    def test_build_steps_matches_full_order(self) -> None:
        args = argparse.Namespace(
            install_deps=False,
            skip_pipeline=False,
            skip_finbert=False,
            skip_lambda=False,
            artifact_uri="",
            force_upload=False,
            snapshot_date="",
            pipeline_timeout_seconds=1800,
        )

        steps = run_all.build_steps(args)

        self.assertEqual(
            [name for name in (script_name(step) for step in steps) if name],
            [
                "create_vpc.py",
                "create_security_groups.py",
                "create_raw_bucket.py",
                "create_features_bucket.py",
                "create_rds_postgres.py",
                "create_glue_connection.py",
                "create_finbert_kinesis.py",
                "deploy_glue_jobs.py",
                "deploy_step_function.py",
                "start_pipeline.py",
                "create_finbert_elastic_ip.py",
                "create_finbert_ec2.py",
                "upload_finbert_artifact.py",
                "deploy_finbert_service.py",
                "deploy_finbert_lambda_consumer.py",
            ],
        )
        self.assertTrue(steps[-3].captures_artifact_uri)
        self.assertTrue(steps[-2].needs_artifact_uri)

    def test_snapshot_date_is_passed_to_start_pipeline(self) -> None:
        args = argparse.Namespace(
            install_deps=False,
            skip_pipeline=False,
            skip_finbert=True,
            skip_lambda=False,
            artifact_uri="",
            force_upload=False,
            snapshot_date="2026-05-20",
            pipeline_timeout_seconds=1800,
        )

        steps = run_all.build_steps(args)
        start_pipeline = next(step for step in steps if "start_pipeline.py" in step.command)

        self.assertIn("--wait-for-output", start_pipeline.command)
        self.assertIn("--timeout-seconds", start_pipeline.command)
        self.assertEqual(start_pipeline.command[-2:], ["--snapshot-date", "2026-05-20"])

    def test_skip_finbert_stops_before_ec2_steps(self) -> None:
        args = argparse.Namespace(
            install_deps=False,
            skip_pipeline=False,
            skip_finbert=True,
            skip_lambda=False,
            artifact_uri="",
            force_upload=False,
            snapshot_date="",
            pipeline_timeout_seconds=1800,
        )

        commands = [" ".join(step.command) for step in run_all.build_steps(args)]

        self.assertFalse(any("create_finbert_ec2.py" in command for command in commands))
        self.assertFalse(any("deploy_finbert_service.py" in command for command in commands))

    def test_existing_artifact_uri_prefers_cli_value(self) -> None:
        args = argparse.Namespace(artifact_uri="s3://bucket/model/")

        with patch.object(run_all, "read_env_file", return_value=([], {"FINBERT_ARTIFACT_URI": "s3://old/model/"})):
            self.assertEqual(run_all.existing_artifact_uri(args), "s3://bucket/model/")

    def test_existing_artifact_uri_reads_env_file(self) -> None:
        args = argparse.Namespace(artifact_uri="")

        with patch.object(run_all, "read_env_file", return_value=([], {"FINBERT_ARTIFACT_URI": "s3://bucket/model/"})):
            self.assertEqual(run_all.existing_artifact_uri(args), "s3://bucket/model/")

    def test_force_upload_is_passed_to_upload_script(self) -> None:
        args = argparse.Namespace(
            install_deps=False,
            skip_pipeline=True,
            skip_finbert=False,
            skip_lambda=True,
            artifact_uri="",
            force_upload=True,
            snapshot_date="",
            pipeline_timeout_seconds=1800,
        )

        upload = next(step for step in run_all.build_steps(args) if "upload_finbert_artifact.py" in step.command)

        self.assertIn("--force-upload", upload.command)

    def test_subprocess_env_loads_env_file_and_overrides_stale_shell_values(self) -> None:
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "stale", "KEEP": "yes"}, clear=True), patch.object(
            run_all,
            "read_env_file",
            return_value=([], {"AWS_ACCESS_KEY_ID": "fresh", "AWS_SECRET_ACCESS_KEY": "secret"}),
        ):
            env = run_all.subprocess_env()

        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "fresh")
        self.assertEqual(env["AWS_SECRET_ACCESS_KEY"], "secret")
        self.assertEqual(env["KEEP"], "yes")
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")

    def test_subprocess_env_ignores_blank_env_file_values(self) -> None:
        with patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "shell-value"}, clear=True), patch.object(
            run_all,
            "read_env_file",
            return_value=([], {"AWS_ACCESS_KEY_ID": ""}),
        ):
            env = run_all.subprocess_env()

        self.assertEqual(env["AWS_ACCESS_KEY_ID"], "shell-value")


if __name__ == "__main__":
    unittest.main()
