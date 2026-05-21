"""Lambda consumer for Kinesis-triggered FinBERT inference."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_RESULT_BUCKET = "proyecto-integrador-2-features-amce"
DEFAULT_RESULT_PREFIX = "inference/finbert/results"
DEFAULT_API_TIMEOUT_SECONDS = 20

s3_client = None


class InferenceError(Exception):
    """Raised for request-level errors that should produce an S3 error object."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def result_key(*, prefix: str, request_id: str, submitted_at: str, processed_at: str) -> str:
    date_source = submitted_at or processed_at
    date = date_source.split("T", 1)[0]
    return f"{prefix.strip('/')}/date={date}/{request_id}.json"


def decode_record(record: dict[str, Any]) -> dict[str, Any]:
    encoded = record.get("kinesis", {}).get("data", "")
    if not encoded:
        raise InferenceError("Kinesis record is missing data.")
    try:
        payload = base64.b64decode(encoded).decode("utf-8")
        decoded = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise InferenceError(f"Kinesis record data is not valid JSON: {error}") from error
    if not isinstance(decoded, dict):
        raise InferenceError("Kinesis record JSON must be an object.")
    return decoded


def validate_request(payload: dict[str, Any]) -> dict[str, str]:
    request_id = str(payload.get("request_id", "")).strip()
    text = str(payload.get("text", "")).strip()
    submitted_at = str(payload.get("submitted_at", "")).strip()
    if not request_id:
        raise InferenceError("request_id is required.")
    if not text:
        raise InferenceError("text is required.")
    return {
        "request_id": request_id,
        "text": text,
        "submitted_at": submitted_at,
    }


def call_finbert_api(*, api_url: str, text: str, timeout_seconds: int) -> dict[str, Any]:
    body = json.dumps({"text": text}).encode("utf-8")
    request = Request(
        api_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
            status = response.status
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise InferenceError(f"FinBERT API returned HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise InferenceError(f"FinBERT API request failed: {error.reason}") from error
    except TimeoutError as error:
        raise InferenceError("FinBERT API request timed out.") from error

    if status < 200 or status >= 300:
        raise InferenceError(f"FinBERT API returned HTTP {status}: {response_body}")

    try:
        decoded = json.loads(response_body)
    except ValueError as error:
        raise InferenceError(f"FinBERT API returned invalid JSON: {error}") from error

    predictions = decoded.get("predictions")
    if not isinstance(predictions, list) or not predictions:
        raise InferenceError("FinBERT API response is missing predictions.")
    prediction = predictions[0]
    if not isinstance(prediction, dict):
        raise InferenceError("FinBERT API prediction is not an object.")
    return prediction


def success_result(*, request: dict[str, str], prediction: dict[str, Any], processed_at: str) -> dict[str, Any]:
    return {
        "request_id": request["request_id"],
        "status": "success",
        "text": request["text"],
        "prediction": {
            "label": prediction.get("label"),
            "score": prediction.get("score"),
            "probabilities": prediction.get("probabilities"),
        },
        "submitted_at": request.get("submitted_at", ""),
        "processed_at": processed_at,
    }


def error_result(
    *,
    request_id: str,
    text: str,
    submitted_at: str,
    error_type: str,
    message: str,
    processed_at: str,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "status": "error",
        "text": text,
        "error": {
            "type": error_type,
            "message": message,
        },
        "submitted_at": submitted_at,
        "processed_at": processed_at,
    }


def write_result(*, bucket: str, prefix: str, result: dict[str, Any]) -> str:
    global s3_client
    if s3_client is None:
        import boto3

        s3_client = boto3.client("s3")
    key = result_key(
        prefix=prefix,
        request_id=result["request_id"],
        submitted_at=result.get("submitted_at", ""),
        processed_at=result["processed_at"],
    )
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )
    return key


def handle_payload(payload: dict[str, Any], *, api_url: str, timeout_seconds: int) -> dict[str, Any]:
    request = validate_request(payload)
    prediction = call_finbert_api(api_url=api_url, text=request["text"], timeout_seconds=timeout_seconds)
    return success_result(request=request, prediction=prediction, processed_at=utc_now())


def fallback_request_values(payload: dict[str, Any] | None, record_index: int) -> tuple[str, str, str]:
    if not isinstance(payload, dict):
        return f"invalid-record-{record_index}", "", ""
    request_id = str(payload.get("request_id", "")).strip() or f"invalid-record-{record_index}"
    text = str(payload.get("text", "")).strip()
    submitted_at = str(payload.get("submitted_at", "")).strip()
    return request_id, text, submitted_at


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    api_url = os.environ["FINBERT_API_URL"]
    result_bucket = os.getenv("FINBERT_RESULT_BUCKET", DEFAULT_RESULT_BUCKET)
    result_prefix = os.getenv("FINBERT_RESULT_PREFIX", DEFAULT_RESULT_PREFIX)
    timeout_seconds = int(os.getenv("FINBERT_API_TIMEOUT_SECONDS", str(DEFAULT_API_TIMEOUT_SECONDS)))
    written_keys = []

    for index, record in enumerate(event.get("Records", [])):
        payload: dict[str, Any] | None = None
        try:
            payload = decode_record(record)
            result = handle_payload(payload, api_url=api_url, timeout_seconds=timeout_seconds)
        except Exception as error:
            request_id, text, submitted_at = fallback_request_values(payload, index)
            result = error_result(
                request_id=request_id,
                text=text,
                submitted_at=submitted_at,
                error_type=type(error).__name__,
                message=str(error),
                processed_at=utc_now(),
            )
        written_keys.append(write_result(bucket=result_bucket, prefix=result_prefix, result=result))

    return {
        "statusCode": 200,
        "body": json.dumps({"written": written_keys}),
    }
