from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from finbert.artifacts import (
    build_feature_snapshot_uri,
    build_model_artifact_uri,
    parse_s3_uri,
    prepare_deployable_artifact,
)


class FinbertArtifactTests(unittest.TestCase):
    def test_parse_s3_uri(self) -> None:
        parsed = parse_s3_uri("s3://bucket-name/models/finbert/")
        self.assertEqual(parsed.bucket, "bucket-name")
        self.assertEqual(parsed.key, "models/finbert")

    def test_build_feature_snapshot_uri(self) -> None:
        self.assertEqual(
            build_feature_snapshot_uri(
                bucket="features-bucket",
                features_prefix="features/financial_sentiment/",
                snapshot_date="2026-05-20",
            ),
            "s3://features-bucket/features/financial_sentiment/model_features/snapshot_date=2026-05-20/",
        )

    def test_build_model_artifact_uri(self) -> None:
        self.assertEqual(
            build_model_artifact_uri(
                bucket="features-bucket",
                artifact_prefix="models/finbert",
                snapshot_date="2026-05-20",
                run_id="20260520-120000",
            ),
            "s3://features-bucket/models/finbert/snapshot_date=2026-05-20/run_id=20260520-120000/",
        )

    def test_prepare_deployable_artifact_excludes_training_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "checkpoint"
            output = root / "deployable"
            checkpoint.mkdir()
            for filename in [
                "config.json",
                "model.safetensors",
                "tokenizer.json",
                "tokenizer_config.json",
                "optimizer.pt",
                "scheduler.pt",
                "rng_state.pth",
                "training_args.bin",
            ]:
                (checkpoint / filename).write_text("{}", encoding="utf-8")

            prepare_deployable_artifact(
                checkpoint_dir=checkpoint,
                output_dir=output,
                metrics={"final_test": {"accuracy": 1.0}},
                metadata={"run_id": "run"},
            )

            self.assertTrue((output / "config.json").exists())
            self.assertTrue((output / "model.safetensors").exists())
            self.assertTrue((output / "tokenizer.json").exists())
            self.assertFalse((output / "optimizer.pt").exists())
            self.assertFalse((output / "scheduler.pt").exists())
            self.assertFalse((output / "rng_state.pth").exists())
            self.assertFalse((output / "training_args.bin").exists())
            self.assertEqual(json.loads((output / "metrics.json").read_text())["final_test"]["accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
