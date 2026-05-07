"""Rich Slack message builder for VSS incident alerts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_MRKDWN_MAX_LEN = 3000

_VERDICT_EMOJI = {
    "confirmed": "\u2705",
    "rejected": "\u274c",
    "verification-failed": "\u26a0\ufe0f",
    "not-confirmed": "\U0001f6ab",
}

_VERDICT_LABEL = {
    "confirmed": "Confirmed",
    "rejected": "Rejected",
    "verification-failed": "Verification Failed",
    "not-confirmed": "Not Confirmed",
}


def _safe_get(data, *keys, default: str = "N/A") -> Any:
    """Safely traverse nested dicts/lists. Returns default on any missing key."""
    current = data
    for key in keys:
        try:
            current = current[key] if not isinstance(current, dict) else current.get(key)
        except (KeyError, IndexError, TypeError):
            return default
        if current is None:
            return default
    return current


def _humanize_category(raw: str) -> str:
    """Convert snake_case category tag to Title Case label."""
    return raw.replace("_", " ").title()


def _truncate_mrkdwn(text: str, max_len: int = _MRKDWN_MAX_LEN) -> str:
    """Truncate text to fit Slack mrkdwn field limit."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 4] + " \u2026"


def _format_timestamp(ts: str | None) -> str:
    if not ts or ts == "N/A":
        return "N/A"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        epoch = int(dt.timestamp())
        return f"<!date^{epoch}^{{date_long_pretty}} {{time_secs}}|{dt.strftime('%Y-%m-%d %H:%M:%S UTC')}>"
    except (ValueError, TypeError):
        return ts


def _verdict_color(verdict: str) -> str:
    mapping = {
        "confirmed": "#e01e5a",
        "rejected": "#2eb67d",
        "verification-failed": "#ecb22e",
        "not-confirmed": "#dddddd",
    }
    return mapping.get(verdict.lower() if verdict else "", "#dddddd")


def build_slack_blocks(incident: dict) -> tuple[list[dict], str, str]:
    """Build Slack Block Kit blocks from an incident payload.

    Returns (blocks, fallback_text, color).
    """
    verdict = str(_safe_get(incident, "info", "verdict", default="unknown")).lower()
    category_raw = str(_safe_get(incident, "category", default="unknown"))
    sensor_id = str(_safe_get(incident, "sensorId"))
    place_name = str(_safe_get(incident, "place", "name"))
    timestamp = str(_safe_get(incident, "timestamp"))
    reasoning = str(_safe_get(incident, "info", "reasoning"))
    video_url = str(_safe_get(incident, "info", "videoSource"))
    prompt = str(_safe_get(incident, "info", "prompt"))

    verdict_emoji = _VERDICT_EMOJI.get(verdict, "\u2753")
    verdict_label = _VERDICT_LABEL.get(verdict, verdict.upper())
    category_label = _humanize_category(category_raw)
    formatted_ts = _format_timestamp(timestamp)
    color = _verdict_color(verdict)

    blocks: list[dict] = []

    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Verdict:*\n{verdict_emoji} `{verdict_label}`"},
            {"type": "mrkdwn", "text": f"*Category:*\n`{category_label}`"},
        ],
    })

    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Sensor ID:*\n`{sensor_id}`"},
            {"type": "mrkdwn", "text": f"*Place:*\n{place_name}"},
            {"type": "mrkdwn", "text": f"*Timestamp:*\n{formatted_ts}"},
        ],
    })

    blocks.append({"type": "divider"})

    if reasoning and reasoning != "N/A":
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate_mrkdwn(f"*\U0001f9e0 VLM Reasoning:*\n> {reasoning}"),
            },
        })

    if prompt and prompt != "N/A":
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": _truncate_mrkdwn(f"*\U0001f50d Detection Prompt:*\n> _{prompt}_"),
            },
        })

    if video_url and video_url != "N/A":
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*\U0001f3ac Video Evidence:*\n<{video_url}|View Video Clip>",
            },
        })

    fallback_text = f"\u26a0\ufe0f {category_label} - {verdict_label} at {place_name}"

    return blocks, fallback_text, color


def build_test_blocks() -> tuple[list[dict], str, str]:
    """Build a test notification message."""
    test_incident = {
        "place": {"name": "Test Location", "type": "test"},
        "category": "test_notification",
        "sensorId": "test-sensor-000",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "info": {
            "verdict": "confirmed",
            "reasoning": "This is a test notification to verify Slack integration is working correctly.",
            "videoSource": "https://example.com/test-video.mp4",
            "prompt": "test notification",
        },
    }
    return build_slack_blocks(test_incident)
