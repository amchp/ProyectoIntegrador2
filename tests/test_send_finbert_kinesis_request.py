from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "infra"))
from infra import send_finbert_kinesis_request as producer


class FakeKinesisClient:
    def __init__(self) -> None:
        self.put_record_calls: list[dict] = []

    def put_record(self, **kwargs):
        self.put_record_calls.append(kwargs)
        return {"SequenceNumber": "123"}


class FakeSession:
    def __init__(self, *, client: FakeKinesisClient) -> None:
        self._client = client

    def client(self, name: str):
        if name != "kinesis":
            raise AssertionError(name)
        return self._client


class SendFinbertKinesisRequestTests(unittest.TestCase):
    def test_build_request_generates_request_id(self) -> None:
        request = producer.build_request(text=" Apple rose. ")
        self.assertTrue(request["request_id"])
        self.assertEqual(request["text"], "Apple rose.")
        self.assertTrue(request["submitted_at"].endswith("Z"))

    def test_build_request_rejects_empty_text(self) -> None:
        with self.assertRaises(ValueError):
            producer.build_request(text=" ")

    def test_expected_result_uri_uses_submitted_date(self) -> None:
        uri = producer.expected_result_uri(
            bucket="bucket",
            prefix="inference/finbert/results",
            request_id="req-1",
            submitted_at="2026-05-21T12:00:00Z",
        )
        self.assertEqual(uri, "s3://bucket/inference/finbert/results/date=2026-05-21/req-1.json")

    def test_send_request_uses_request_id_partition_key(self) -> None:
        fake_client = FakeKinesisClient()
        request = {
            "request_id": "req-1",
            "text": "Apple rose.",
            "submitted_at": "2026-05-21T12:00:00Z",
        }
        response = producer.send_request(fake_client, stream_name="stream", request=request)

        self.assertEqual(response["SequenceNumber"], "123")
        self.assertEqual(len(fake_client.put_record_calls), 1)
        call = fake_client.put_record_calls[0]
        self.assertEqual(call["StreamName"], "stream")
        self.assertEqual(call["PartitionKey"], "req-1")
        payload = json.loads(call["Data"].decode("utf-8"))
        self.assertEqual(payload["request_id"], "req-1")
        self.assertEqual(payload["text"], "Apple rose.")


if __name__ == "__main__":
    unittest.main()
