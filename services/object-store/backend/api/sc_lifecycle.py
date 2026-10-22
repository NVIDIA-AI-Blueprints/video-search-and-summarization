"""Bucket lifecycle backstop for archived Smart City frames.

sc_service tags every archived ITS frame except KPI-set ones with
deft-retention=its-frame. With SC_LIFECYCLE_ENABLED the backend keeps one
rule in the bucket's lifecycle configuration that expires objects under the
ITS prefix carrying that tag once they are older than the retention age plus
the hold plus a day. sc_gc normally deletes them well before that; the rule
catches what it misses (GC switched off on one host, a pass that keeps
failing). AOI images, snapshots, KPI manifests and untagged frames never
match: the filter needs both the prefix and the tag.

The rule is evaluated by SeaweedFS's lifecycle worker (`weed admin` plus
`weed worker -jobType=s3_lifecycle`), which re-reads tags when it scans.
SeaweedFS 4.17 and older turn a rule with a tag filter into a TTL on the
whole bucket, so every object written afterwards would expire, AOI images
included; 4.18 to 4.23 accept the rule but nothing enforces it. The rule is
therefore only written after the master reports 4.24 or later.

The bucket is shared, so the configuration is read and only this rule (by
ID) is added, replaced or removed; other rules are kept as they are.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

import httpx
from deft_common import Tag

from backend.config import settings
from backend.shared.storage import StorageService

logger = logging.getLogger(__name__)

FRAME_TAG_KEY = "deft-retention"
FRAME_TAG_VALUE = "its-frame"
RULE_ID = "deft-its-frame-expiry"
MIN_SEAWEEDFS_VERSION = (4, 24)

_KPI_TAGS = frozenset({Tag.KPI, Tag.KPI_GOLDEN})
# Version() is "<size limit> <major>.<minor> <commit>", e.g. "30GB 4.48 1a2b3c".
_VERSION = re.compile(r"(?<![\d.])(\d+)\.(\d+)(?![\d.])")


def frame_tagging(tag: str | None) -> str | None:
    """The S3 tag set for an archived ITS frame, or None for a KPI-set frame
    (never expired by the rule) or when tagging is off."""
    if not settings.sc_frame_tagging or tag in _KPI_TAGS:
        return None
    return f"{FRAME_TAG_KEY}={FRAME_TAG_VALUE}"


def expiry_days() -> int:
    """Retention plus hold, rounded up, plus a day, so sc_gc deletes first."""
    return math.ceil(settings.sc_gc_retention_days + settings.sc_gc_hold_s / 86400) + 1


def lifecycle_rule() -> dict[str, Any]:
    return {
        "ID": RULE_ID,
        "Status": "Enabled",
        "Filter": {
            "And": {
                "Prefix": f"{settings.its_object_prefix}/",
                "Tags": [{"Key": FRAME_TAG_KEY, "Value": FRAME_TAG_VALUE}],
            }
        },
        "Expiration": {"Days": expiry_days()},
    }


def _same_rule(stored: dict[str, Any], wanted: dict[str, Any]) -> bool:
    """Compare what this module sets, ignoring defaults the store fills in."""

    def key(rule: dict[str, Any]) -> tuple[Any, ...]:
        and_ = (rule.get("Filter") or {}).get("And") or {}
        tags = sorted((t.get("Key"), t.get("Value")) for t in and_.get("Tags") or [])
        return (
            rule.get("Status"),
            and_.get("Prefix"),
            tuple(tags),
            (rule.get("Expiration") or {}).get("Days"),
        )

    return key(stored) == key(wanted)


def parse_version(text: str) -> tuple[int, int] | None:
    match = _VERSION.search(text or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


async def seaweedfs_version() -> tuple[int, int] | None:
    """The version the SeaweedFS master reports, or None if it cannot be read."""
    if not settings.seaweedfs_master_url:
        return None
    url = f"{settings.seaweedfs_master_url.rstrip('/')}/dir/status"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return parse_version(str(resp.json().get("Version", "")))
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        logger.warning("could not read the SeaweedFS version from %s: %s", url, exc)
        return None


async def sync_lifecycle_rule(storage: StorageService) -> str:
    """Add, update or remove the ITS rule to match the settings.

    Returns what was done; a result starting with "refused" means the rule
    was wanted but not written.
    """
    rules = await storage.get_lifecycle_rules()
    ours = [r for r in rules if r.get("ID") == RULE_ID]
    others = [r for r in rules if r.get("ID") != RULE_ID]

    if not settings.sc_lifecycle_enabled:
        if not ours:
            return "off"
        await storage.put_lifecycle_rules(others)
        return "removed"

    if not settings.sc_gc_enabled:
        # Without the pass, expired objects would leave catalog documents that
        # snapshots still select and fine-tuning silently skips.
        return "refused: SC_LIFECYCLE_ENABLED needs SC_GC_ENABLED"
    version = await seaweedfs_version()
    if version is None:
        return "refused: the SeaweedFS version could not be read (set SEAWEEDFS_MASTER_URL)"
    if version < MIN_SEAWEEDFS_VERSION:
        return (
            f"refused: SeaweedFS {version[0]}.{version[1]:02d} is older than "
            f"{MIN_SEAWEEDFS_VERSION[0]}.{MIN_SEAWEEDFS_VERSION[1]}; a tag-filtered "
            "rule would expire the whole bucket or not run at all"
        )

    wanted = lifecycle_rule()
    if len(ours) == 1 and _same_rule(ours[0], wanted):
        return "unchanged"
    await storage.put_lifecycle_rules([*others, wanted])
    return f"set: tagged ITS frames expire after {wanted['Expiration']['Days']} days"
