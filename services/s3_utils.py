import os
import tempfile
from typing import Tuple
from urllib.parse import urlparse

import boto3


def parse_s3_location(file_path: str) -> Tuple[str, str]:
    """
    Returns (bucket, key).

    Supports:
      - s3://bucket/key
      - https://bucket.s3.amazonaws.com/key (and similar)
      - raw key (requires AWS_BUCKET or S3_BUCKET env var)
    """
    file_path = (file_path or "").strip()
    if file_path.startswith("s3://"):
        parsed = urlparse(file_path)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        if not bucket or not key:
            raise ValueError(f"Invalid s3 URL: {file_path}")
        return bucket, key

    if file_path.startswith("http://") or file_path.startswith("https://"):
        parsed = urlparse(file_path)
        host_parts = (parsed.netloc or "").split(".")
        bucket = host_parts[0] if host_parts else ""
        key = parsed.path.lstrip("/")
        if bucket and key and "s3" in parsed.netloc:
            return bucket, key

    bucket = (os.getenv("AWS_BUCKET") or os.getenv("S3_BUCKET") or "").strip()
    if not bucket:
        raise ValueError(
            "AWS_BUCKET/S3_BUCKET is not set, and file_path is not a recognizable S3 URL."
        )
    key = file_path.lstrip("/")
    if not key:
        raise ValueError("Empty S3 key from file_path.")
    return bucket, key


def download_from_s3(file_path: str) -> str:
    """
    Downloads the object referenced by file_path to a temp file and returns local path.
    Uses AWS_REGION/AWS_DEFAULT_REGION if set; credentials are handled by boto3.
    """
    bucket, key = parse_s3_location(file_path)
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    s3 = boto3.client("s3", region_name=region) if region else boto3.client("s3")

    suffix = os.path.splitext(key)[1] or ""
    fd, local_path = tempfile.mkstemp(prefix="s3_", suffix=suffix)
    os.close(fd)

    s3.download_file(bucket, key, local_path)
    return local_path


def upload_local_file(local_path: str, s3_key: str) -> str:
    """
    Upload a local file to S3. Returns s3://bucket/key
    """
    bucket = (os.getenv("AWS_BUCKET") or os.getenv("S3_BUCKET") or "").strip()
    if not bucket:
        raise ValueError("AWS_BUCKET or S3_BUCKET must be set for uploads.")

    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    s3 = boto3.client("s3", region_name=region) if region else boto3.client("s3")

    key = s3_key.lstrip("/")
    s3.upload_file(local_path, bucket, key)
    return f"s3://{bucket}/{key}"

