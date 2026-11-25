"""Schemas for POST /v1/get_presigned_urls — object-store read access."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.config import settings

# Safe charsets so the key can't escape the deft-aoi/ prefix ('/', '..' excluded).
# folder: a single path segment. file_name: additionally allows '.' (extensions);
# the literal '..' is still rejected in the validator.
_FOLDER_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class PresignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # objStorePath keys or full object-store URLs the caller wants to download.
    # max_length is the single source of truth for the per-request batch cap.
    urls: list[str] = Field(
        min_length=1, max_length=settings.max_presign_urls_per_request
    )
    expires_in: int | None = Field(
        default=None,
        ge=60,
        le=86400,
        description="Presigned URL lifetime in seconds (default from server config).",
    )


class PresignedUrlItem(BaseModel):
    input: str
    key: str
    presigned_url: str
    expires_at: str


class PresignFailureItem(BaseModel):
    input: str
    error: str


class PresignResponse(BaseModel):
    items: list[PresignedUrlItem]
    failed: list[PresignFailureItem]


class PresignPostRequest(BaseModel):
    """POST /v1/get_presigned_post_url — direct client upload of one object."""

    model_config = ConfigDict(extra="forbid")

    content_type: str = Field(min_length=1)
    folder: str
    file_name: str

    @field_validator("folder")
    @classmethod
    def _valid_folder(cls, v: str) -> str:
        if not _FOLDER_RE.fullmatch(v):
            raise ValueError(
                "folder must be a single path segment of [A-Za-z0-9_-] "
                "(no nesting, no '/', no '..')"
            )
        return v

    @field_validator("file_name")
    @classmethod
    def _valid_file_name(cls, v: str) -> str:
        if not _FILE_NAME_RE.fullmatch(v) or ".." in v:
            raise ValueError(
                "file_name must contain only [A-Za-z0-9._-] (no '/', no '..')"
            )
        return v


class PresignPostResponse(BaseModel):
    url: str
    fields: dict[str, str]
