from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
INFRA_DIR = ROOT / "infra"
sys.path.insert(0, str(INFRA_DIR))

import start_pipeline


class StartPipelineWaitTests(unittest.TestCase):
    def test_final_feature_report_key_uses_snapshot_partition(self) -> None:
        self.assertEqual(
            start_pipeline.final_feature_report_key(
                features_prefix="features/financial_sentiment",
                snapshot_date="2026-05-20",
            ),
            "features/financial_sentiment/reports/snapshot_date=2026-05-20/split_distribution.csv",
        )

    def test_wait_for_state_machine_prefers_configured_arn(self) -> None:
        client = Mock()

        with patch.object(start_pipeline, "configured_state_machine_arn", return_value="arn:aws:states:::stateMachine:test"):
            arn = start_pipeline.wait_for_state_machine_arn(
                client,
                state_machine_name="name",
                poll_seconds=1,
            )

        self.assertEqual(arn, "arn:aws:states:::stateMachine:test")
        client.describe_state_machine.assert_called_once_with(
            stateMachineArn="arn:aws:states:::stateMachine:test"
        )

    def test_wait_for_state_machine_uses_list_fallback(self) -> None:
        client = Mock()
        paginator = Mock()
        paginator.paginate.return_value = [
            {"stateMachines": [{"name": "name", "stateMachineArn": "arn"}]}
        ]
        client.get_paginator.return_value = paginator

        with patch.object(start_pipeline, "configured_state_machine_arn", return_value=""):
            arn = start_pipeline.wait_for_state_machine_arn(
                client,
                state_machine_name="name",
                poll_seconds=1,
            )

        self.assertEqual(arn, "arn")


if __name__ == "__main__":
    unittest.main()
