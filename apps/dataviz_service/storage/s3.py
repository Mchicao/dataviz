"""Backend productivo S3 compatible para artefactos inmutables."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from apps.dataviz_service.contracts import ResourceNotFoundError


class S3ObjectBackend:
    """Implementa ``ObjectBackend`` con escrituras condicionales atómicas."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = "us-east-1",
        allow_http: bool = False,
        client: Any | None = None,
    ) -> None:
        parsed = urlparse(endpoint_url)
        allowed_schemes = {"https"} | ({"http"} if allow_http else set())
        if parsed.scheme not in allowed_schemes or not parsed.hostname:
            raise ValueError("S3 endpoint must be an explicit HTTPS URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("S3 endpoint must not contain credentials, query or fragment")
        if not bucket or not access_key_id or not secret_access_key:
            raise ValueError("S3 bucket and credentials are required")
        self._bucket = bucket
        self._client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            use_ssl=parsed.scheme == "https",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def put_if_absent(self, key: str, data: bytes) -> bool:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                IfNoneMatch="*",
                ChecksumAlgorithm="SHA256",
            )
            return True
        except ClientError as exc:
            if _status(exc) in {409, 412}:
                return False
            raise

    def ready(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return True
        except ClientError:
            return False

    def get(self, key: str) -> bytes:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            if _status(exc) == 404 or exc.response.get("Error", {}).get("Code") == "NoSuchKey":
                raise ResourceNotFoundError("object", key) from exc
            raise

    def delete(self, key: str) -> None:
        if not self.exists(key):
            raise ResourceNotFoundError("object", key)
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if _status(exc) == 404:
                return False
            raise

    def list(self, prefix: str) -> list[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self._bucket, Prefix=prefix)
        return sorted(item["Key"] for page in pages for item in page.get("Contents", []))


def _status(exc: ClientError) -> int:
    return int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
