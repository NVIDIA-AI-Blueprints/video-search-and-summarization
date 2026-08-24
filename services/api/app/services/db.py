import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key

from app.core.config import get_settings
from app.models.video import VideoMetadata


def _deserialize(item: dict[str, Any]) -> VideoMetadata:
    return VideoMetadata.model_validate(item)


class VideoRepository:
    """DynamoDB-backed store for video metadata.

    Key schema (single table): pk = USER#<owner_id>, sk = VIDEO#<video_id>.
    """

    def __init__(self) -> None:
        settings = get_settings()
        endpoint = os.environ.get("DYNAMODB_ENDPOINT_URL")
        resource = boto3.resource(
            "dynamodb",
            region_name=settings.aws_region,
            endpoint_url=endpoint,
        )
        self._table = resource.Table(settings.videos_table)

    def create(self, video: VideoMetadata) -> VideoMetadata:
        self._table.put_item(Item=video.model_dump())
        return video

    def get(self, owner_id: str, video_id: str) -> VideoMetadata | None:
        response = self._table.get_item(Key={"pk": f"USER#{owner_id}", "sk": f"VIDEO#{video_id}"})
        item = response.get("Item")
        return _deserialize(item) if item else None

    def list_for_owner(self, owner_id: str, limit: int = 100) -> list[VideoMetadata]:
        response = self._table.query(
            KeyConditionExpression=Key("pk").eq(f"USER#{owner_id}"),
            FilterExpression=Attr("entity_type").eq("video"),
            Limit=limit,
            ScanIndexForward=False,
        )
        return [_deserialize(item) for item in response.get("Items", [])]

    def update_fields(self, owner_id: str, video_id: str, fields: dict[str, Any]) -> None:
        expr_names = {f"#{k}": k for k in fields}
        update_expr = "SET " + ", ".join(f"#{k} = :{k}" for k in fields)
        self._table.update_item(
            Key={"pk": f"USER#{owner_id}", "sk": f"VIDEO#{video_id}"},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
        )

    def delete(self, owner_id: str, video_id: str) -> None:
        self._table.delete_item(Key={"pk": f"USER#{owner_id}", "sk": f"VIDEO#{video_id}"})
