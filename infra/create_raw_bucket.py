#!/usr/bin/env python3
"""Create the raw-data S3 bucket with low-cost defaults."""

from __future__ import annotations

from utils.common import resolve_region
from utils.s3 import create_bucket

AWS_REGION = "us-east-1"
BUCKET_NAME = "proyecto-integrador-2"
BUCKET_TAGS = {
    "Project": "ProyectoDeGrado",
    "Layer": "raw",
    "ManagedBy": "python-script",
}


if __name__ == "__main__":
    create_bucket(
        bucket_name=BUCKET_NAME,
        region=resolve_region(AWS_REGION),
        tags=BUCKET_TAGS,
        label="Raw",
    )
