from __future__ import annotations

import base64
import importlib
import json
import unittest
from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import patch


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: list[dict] = []

    def put_object(self, **kwargs):
        self.objects.append(kwargs)
        return {}


class FakeHttpResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(
            {
                "predictions": [
                    {
                        "label": "positive",
                        "score": 0.97,
                        "probabilities": {"negative": 0.01, "neutral": 0.02, "positive": 0.97},
                    }
                ]
            }
        ).encode("utf-8")


def kinesis_event(payload: dict) -> dict:
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return {"Records": [{"kinesis": {"data": encoded}}]}


class FinbertLambdaConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = importlib.import_module("infra.lambda.finbert_inference_consumer")
        self.fake_s3 = FakeS3Client()
        self.module.s3_client = self.fake_s3
        self.env = {
            "FINBERT_API_URL": "http://10.0.0.10:8000/predict",
            "FINBERT_RESULT_BUCKET": "bucket",
            "FINBERT_RESULT_PREFIX": "inference/finbert/results",
        }

    def written_body(self) -> dict:
        self.assertEqual(len(self.fake_s3.objects), 1)
        return json.loads(self.fake_s3.objects[0]["Body"].decode("utf-8"))

    def test_valid_event_calls_api_and_writes_success_result(self) -> None:
        with patch.dict(self.module.os.environ, self.env, clear=True), patch.object(
            self.module, "urlopen", return_value=FakeHttpResponse()
        ) as urlopen:
            self.module.lambda_handler(
                kinesis_event(
                    {
                        "request_id": "req-1",
                        "text": "Apple shares rose after earnings beat expectations.",
                        "submitted_at": "2026-05-21T12:00:00Z",
                    }
                ),
                None,
            )

        urlopen.assert_called_once()
        body = self.written_body()
        self.assertEqual(body["request_id"], "req-1")
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["prediction"]["label"], "positive")

    def test_missing_request_id_writes_error_result(self) -> None:
        with patch.dict(self.module.os.environ, self.env, clear=True):
            self.module.lambda_handler(kinesis_event({"text": "Apple rose."}), None)

        body = self.written_body()
        self.assertEqual(body["request_id"], "invalid-record-0")
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error"]["type"], "InferenceError")
        self.assertIn("request_id is required", body["error"]["message"])

    def test_empty_text_writes_error_result(self) -> None:
        with patch.dict(self.module.os.environ, self.env, clear=True):
            self.module.lambda_handler(kinesis_event({"request_id": "req-2", "text": "  "}), None)

        body = self.written_body()
        self.assertEqual(body["request_id"], "req-2")
        self.assertEqual(body["status"], "error")
        self.assertIn("text is required", body["error"]["message"])

    def test_api_timeout_writes_error_result(self) -> None:
        with patch.dict(self.module.os.environ, self.env, clear=True), patch.object(
            self.module, "urlopen", side_effect=TimeoutError()
        ):
            self.module.lambda_handler(kinesis_event({"request_id": "req-3", "text": "Apple rose."}), None)

        body = self.written_body()
        self.assertEqual(body["request_id"], "req-3")
        self.assertEqual(body["status"], "error")
        self.assertIn("timed out", body["error"]["message"])

    def test_api_non_200_writes_error_result(self) -> None:
        error = HTTPError(
            url="http://10.0.0.10:8000/predict",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=BytesIO(b'{"detail":"Model is not loaded."}'),
        )
        with patch.dict(self.module.os.environ, self.env, clear=True), patch.object(
            self.module, "urlopen", side_effect=error
        ):
            self.module.lambda_handler(kinesis_event({"request_id": "req-4", "text": "Apple rose."}), None)

        body = self.written_body()
        self.assertEqual(body["request_id"], "req-4")
        self.assertEqual(body["status"], "error")
        self.assertIn("HTTP 503", body["error"]["message"])

    def test_result_key_uses_date_and_request_id(self) -> None:
        key = self.module.result_key(
            prefix="inference/finbert/results",
            request_id="req-5",
            submitted_at="2026-05-21T12:00:00Z",
            processed_at="2026-05-21T12:00:04Z",
        )
        self.assertEqual(key, "inference/finbert/results/date=2026-05-21/req-5.json")

    def test_result_key_falls_back_to_processed_date(self) -> None:
        key = self.module.result_key(
            prefix="inference/finbert/results",
            request_id="req-6",
            submitted_at="",
            processed_at="2026-05-22T00:00:04Z",
        )
        self.assertEqual(key, "inference/finbert/results/date=2026-05-22/req-6.json")


if __name__ == "__main__":
    unittest.main()
