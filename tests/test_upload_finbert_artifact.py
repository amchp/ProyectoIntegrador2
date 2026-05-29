from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INFRA_DIR = ROOT / "infra"
sys.path.insert(0, str(INFRA_DIR))

import upload_finbert_artifact


class FakeS3Client:
    def __init__(self, existing_keys: set[tuple[str, str]]) -> None:
        self.existing_keys = existing_keys

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        if (Bucket, Key) not in self.existing_keys:
            error = Exception("not found")
            error.response = {"Error": {"Code": "404"}}
            raise error
        return {}


class UploadFinbertArtifactTests(unittest.TestCase):
    def test_parse_s3_uri(self) -> None:
        self.assertEqual(
            upload_finbert_artifact.parse_s3_uri("s3://bucket/models/finbert/"),
            ("bucket", "models/finbert"),
        )

    def test_artifact_files_exist_requires_all_selected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            files = [
                model_dir / "config.json",
                model_dir / "model.safetensors",
                model_dir / "tokenizer.json",
            ]
            for path in files:
                path.write_text("{}", encoding="utf-8")

            s3_client = FakeS3Client(
                {
                    ("bucket", "models/finbert/run_id=1/config.json"),
                    ("bucket", "models/finbert/run_id=1/model.safetensors"),
                    ("bucket", "models/finbert/run_id=1/tokenizer.json"),
                }
            )

            self.assertTrue(
                upload_finbert_artifact.artifact_files_exist(
                    s3_client,
                    artifact_uri="s3://bucket/models/finbert/run_id=1/",
                    files=files,
                )
            )

    def test_artifact_files_exist_returns_false_when_any_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp)
            files = [
                model_dir / "config.json",
                model_dir / "model.safetensors",
            ]
            for path in files:
                path.write_text("{}", encoding="utf-8")

            s3_client = FakeS3Client({("bucket", "models/finbert/run_id=1/config.json")})

            self.assertFalse(
                upload_finbert_artifact.artifact_files_exist(
                    s3_client,
                    artifact_uri="s3://bucket/models/finbert/run_id=1/",
                    files=files,
                )
            )


if __name__ == "__main__":
    unittest.main()
