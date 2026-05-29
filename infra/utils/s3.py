"""Shared S3 provisioning helpers."""

from __future__ import annotations

from botocore.exceptions import ClientError

from utils.aws import aws_client
from utils.common import ensure, serialize_tags


def _ignore_client_error(error: ClientError, *codes: str) -> bool:
    return error.response["Error"]["Code"] in codes


def _bucket_create_request(bucket_name: str, region: str) -> dict:
    request = {"Bucket": bucket_name}
    if region != "us-east-1":
        request["CreateBucketConfiguration"] = {"LocationConstraint": region}
    return request


def _configure_bucket(s3_client, bucket_name: str, tags: dict[str, str]) -> dict:
    s3_client.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3_client.put_bucket_encryption(
        Bucket=bucket_name,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                    "BucketKeyEnabled": True,
                }
            ]
        },
    )
    s3_client.put_bucket_lifecycle_configuration(
        Bucket=bucket_name,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "abort-incomplete-multipart-uploads",
                    "Filter": {"Prefix": ""},
                    "Status": "Enabled",
                    "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
                }
            ]
        },
    )
    s3_client.put_bucket_tagging(
        Bucket=bucket_name,
        Tagging={"TagSet": serialize_tags(tags)},
    )
    return {"Bucket": bucket_name}


def create_bucket(
    bucket_name: str,
    region: str,
    tags: dict[str, str],
    label: str,
) -> None:
    s3_client = aws_client("s3", region=region)

    def check() -> dict | None:
        try:
            s3_client.head_bucket(Bucket=bucket_name)
            print(f"Bucket {bucket_name} already exists in this account. Reusing it.")
            return {"Bucket": bucket_name}
        except ClientError as error:
            code = error.response["Error"]["Code"]
            if code in ("404", "NoSuchBucket", "NotFound"):
                return None
            if code in ("403", "AccessDenied"):
                raise RuntimeError(
                    f"Bucket name {bucket_name} is already taken by another AWS account."
                ) from error
            raise

    def setup(bucket: dict) -> dict:
        return _configure_bucket(s3_client, bucket["Bucket"], tags)

    def create() -> dict:
        try:
            s3_client.create_bucket(**_bucket_create_request(bucket_name, region))
            print(f"Created bucket {bucket_name} in {region}.")
        except ClientError as error:
            if _ignore_client_error(error, "BucketAlreadyOwnedByYou"):
                print(f"Bucket {bucket_name} already exists in this account. Reusing it.")
            elif _ignore_client_error(error, "BucketAlreadyExists"):
                raise RuntimeError(
                    f"Bucket name {bucket_name} is already taken by another AWS account."
                ) from error
            else:
                raise
        return {"Bucket": bucket_name}

    ensure(
        check,
        create,
        setup=setup,
    )
    print(f"{label} bucket ready: s3://{bucket_name}")
