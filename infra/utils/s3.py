"""Shared S3 provisioning helpers."""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from utils.common import serialize_tags


def create_bucket(
    bucket_name: str,
    region: str,
    tags: dict[str, str],
    label: str,
) -> None:
    s3_client = boto3.Session(region_name=region).client("s3")
    request = {"Bucket": bucket_name}
    if region != "us-east-1":
        request["CreateBucketConfiguration"] = {"LocationConstraint": region}

    try:
        s3_client.create_bucket(**request)
        print(f"Created bucket {bucket_name} in {region}.")
    except ClientError as error:
        code = error.response["Error"]["Code"]
        if code == "BucketAlreadyOwnedByYou":
            print(f"Bucket {bucket_name} already exists in this account. Reusing it.")
        if code == "BucketAlreadyExists":
            raise RuntimeError(
                f"Bucket name {bucket_name} is already taken by another AWS account."
            ) from error
        raise

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
    print(f"{label} bucket ready: s3://{bucket_name}")
