import json

import boto3

from app.core.config import get_settings


class StorageService:
    def __init__(self) -> None:
        settings = get_settings()
        self._media_bucket = settings.media_bucket
        self._artifacts_bucket = settings.artifacts_bucket
        self._s3 = None

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = boto3.client("s3", region_name=get_settings().aws_region)
        return self._s3

    def media_key(self, owner_id: str, video_id: str, filename: str) -> str:
        safe_name = filename.rsplit("/", 1)[-1]
        return f"videos/{owner_id}/{video_id}/{safe_name}"

    def presign_upload(self, key: str, content_type: str, expires: int = 3600) -> str:
        return self.s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._media_bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires,
        )

    def presign_download(self, bucket: str, key: str, expires: int = 3600) -> str:
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires,
        )

    def media_stream_url(self, owner_id: str, video_id: str, filename: str) -> str:
        key = self.media_key(owner_id, video_id, filename)
        return self.presign_download(self._media_bucket, key)

    def get_json(self, key: str):
        response = self.s3.get_object(Bucket=self._artifacts_bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._media_bucket, Prefix=prefix):
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if objects:
                self.s3.delete_objects(Delete={"Objects": objects})
