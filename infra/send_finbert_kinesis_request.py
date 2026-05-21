#!/usr/bin/env python3
"""Send one FinBERT inference request to Kinesis."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from uuid import uuid4

from utils.common import resolve_region

AWS_REGION = "us-east-1"
DEFAULT_STREAM_NAME = "proyecto-finbert-inference-requests"
DEFAULT_RESULT_BUCKET = "proyecto-integrador-2-features-amce"
DEFAULT_RESULT_PREFIX = "inference/finbert/results"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send one FinBERT inference request to Kinesis.")
    parser.add_argument("--text", required=True, help="Financial text to classify.")
    parser.add_argument("--request-id", default="", help="Optional stable request id. Defaults to a UUID.")
    parser.add_argument("--stream-name", default=DEFAULT_STREAM_NAME)
    parser.add_argument("--result-bucket", default=DEFAULT_RESULT_BUCKET)
    parser.add_argument("--result-prefix", default=DEFAULT_RESULT_PREFIX)
    return parser.parse_args()


def build_request(*, text: str, request_id: str = "") -> dict[str, str]:
    trimmed_text = text.strip()
    if not trimmed_text:
        raise ValueError("--text cannot be empty.")
    return {
        "request_id": request_id or str(uuid4()),
        "text": trimmed_text,
        "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def expected_result_uri(*, bucket: str, prefix: str, request_id: str, submitted_at: str) -> str:
    date = submitted_at.split("T", 1)[0]
    return f"s3://{bucket}/{prefix.strip('/')}/date={date}/{request_id}.json"


def send_request(kinesis_client, *, stream_name: str, request: dict[str, str]) -> dict:
    payload = json.dumps(request, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return kinesis_client.put_record(
        StreamName=stream_name,
        Data=payload,
        PartitionKey=request["request_id"],
    )


def main() -> int:
    import boto3

    args = parse_args()
    request = build_request(text=args.text, request_id=args.request_id)
    kinesis_client = boto3.Session(region_name=resolve_region(AWS_REGION)).client("kinesis")
    response = send_request(kinesis_client, stream_name=args.stream_name, request=request)

    print(f"request_id={request['request_id']}")
    print(f"sequence_number={response['SequenceNumber']}")
    print(
        "expected_result_uri="
        + expected_result_uri(
            bucket=args.result_bucket,
            prefix=args.result_prefix,
            request_id=request["request_id"],
            submitted_at=request["submitted_at"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
