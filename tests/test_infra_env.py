from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
INFRA_DIR = ROOT / "infra"
sys.path.insert(0, str(INFRA_DIR))

import create_rds_postgres
import create_vpc
from utils import common


class InfraEnvFileTests(unittest.TestCase):
    def test_write_env_values_preserves_comments_and_updates_existing_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# credentials\nAWS_ACCESS_KEY_ID=old\n\nEC2_KEY_NAME=my-key\n",
                encoding="utf-8",
            )

            common.write_env_values({"AWS_ACCESS_KEY_ID": "new"}, path=path)

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "# credentials\nAWS_ACCESS_KEY_ID=new\n\nEC2_KEY_NAME=my-key\n",
            )

    def test_write_env_values_appends_missing_keys_and_preserves_unrelated_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("AWS_ACCESS_KEY_ID=key\n", encoding="utf-8")

            common.write_env_values(
                {
                    "VPC_ID": "vpc-123",
                    "PUBLIC_SUBNET_IDS": "subnet-a,subnet-b",
                },
                path=path,
            )

            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "AWS_ACCESS_KEY_ID=key\n\nVPC_ID=vpc-123\nPUBLIC_SUBNET_IDS=subnet-a,subnet-b\n",
            )

    def test_read_env_file_parses_quoted_and_unquoted_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "AWS_REGION='us-east-1'\nEC2_KEY_PATH=\"/tmp/key.pem\"\n",
                encoding="utf-8",
            )

            _, values = common.read_env_file(path)

            self.assertEqual(values["AWS_REGION"], "us-east-1")
            self.assertEqual(values["EC2_KEY_PATH"], "/tmp/key.pem")

    def test_persist_env_values_updates_runtime_env_without_printing_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            common, "env_path", return_value=Path(tmp) / ".env"
        ), patch.dict(os.environ, {}, clear=True):
            output = io.StringIO()
            with redirect_stdout(output):
                common.persist_env_values(
                    {"RDS_MASTER_PASSWORD": "super-secret"},
                    secret_keys={"RDS_MASTER_PASSWORD"},
                )

            self.assertEqual(os.environ["RDS_MASTER_PASSWORD"], "super-secret")
            self.assertIn("RDS_MASTER_PASSWORD", output.getvalue())
            self.assertNotIn("super-secret", output.getvalue())

    def test_generate_secret_is_shell_safe(self) -> None:
        secret = common.generate_secret(64)

        self.assertEqual(len(secret), 64)
        self.assertRegex(secret, r"^[A-Za-z0-9_-]+$")

    def test_load_local_env_overrides_stale_shell_values_with_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            common, "env_path", return_value=Path(tmp) / ".env"
        ), patch.dict(os.environ, {"AWS_ACCESS_KEY_ID": "stale"}, clear=True):
            Path(tmp, ".env").write_text("AWS_ACCESS_KEY_ID=fresh\n", encoding="utf-8")
            common._ENV_LOADED = False
            try:
                common.load_local_env()
                self.assertEqual(os.environ["AWS_ACCESS_KEY_ID"], "fresh")
            finally:
                common._ENV_LOADED = False


class InfraScriptPersistenceTests(unittest.TestCase):
    def test_create_vpc_persists_vpc_outputs(self) -> None:
        resources = {
            "vpc_id": "vpc-123",
            "public_subnet_ids": ["subnet-public-a", "subnet-public-b"],
            "private_subnet_ids": ["subnet-private-a", "subnet-private-b"],
            "s3_gateway_endpoint_id": "vpce-123",
        }

        with patch.object(create_vpc, "resolve_region", return_value="us-east-1"), patch.object(
            create_vpc, "create_vpc_stack", return_value=resources
        ), patch.object(create_vpc, "persist_env_values") as persist, redirect_stdout(io.StringIO()):
            create_vpc.main()

        persist.assert_called_once_with(
            {
                "AWS_REGION": "us-east-1",
                "VPC_ID": "vpc-123",
                "PUBLIC_SUBNET_IDS": "subnet-public-a,subnet-public-b",
                "PRIVATE_SUBNET_IDS": "subnet-private-a,subnet-private-b",
            }
        )

    def test_create_rds_generates_master_password_when_missing(self) -> None:
        with patch.object(
            create_rds_postgres,
            "require_env",
            side_effect=ValueError("missing"),
        ), patch.object(
            create_rds_postgres,
            "generate_secret",
            return_value="generated-password",
        ), patch.object(create_rds_postgres, "persist_env_values") as persist:
            password = create_rds_postgres.resolve_master_password()

        self.assertEqual(password, "generated-password")
        persist.assert_called_once_with(
            {"RDS_MASTER_PASSWORD": "generated-password"},
            secret_keys={"RDS_MASTER_PASSWORD"},
        )

    def test_role_deploy_scripts_use_labrole_helpers(self) -> None:
        role_scripts = [
            INFRA_DIR / "deploy_glue_jobs.py",
            INFRA_DIR / "deploy_step_function.py",
            INFRA_DIR / "deploy_finbert_lambda_consumer.py",
            INFRA_DIR / "create_finbert_ec2.py",
        ]

        for script in role_scripts:
            source = script.read_text(encoding="utf-8")
            self.assertNotIn("GLUE_SERVICE_ROLE_ARN", source)
            self.assertNotIn("STATE_MACHINE_ROLE_ARN", source)
            self.assertNotIn("LAMBDA_EXECUTION_ROLE_ARN", source)
            self.assertNotIn("SAGEMAKER_EXECUTION_ROLE_ARN", source)

        self.assertIn("lab_role_arn", (INFRA_DIR / "deploy_glue_jobs.py").read_text(encoding="utf-8"))
        self.assertIn("lab_role_arn", (INFRA_DIR / "deploy_step_function.py").read_text(encoding="utf-8"))
        self.assertIn("lab_role_arn", (INFRA_DIR / "deploy_finbert_lambda_consumer.py").read_text(encoding="utf-8"))
        self.assertIn("lab_instance_profile_name", (INFRA_DIR / "create_finbert_ec2.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
