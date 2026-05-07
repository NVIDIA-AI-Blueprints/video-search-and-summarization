"""Alert Notify Slack - Webhook server that receives VSS incidents and posts rich Slack notifications."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import signal
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from slack_formatter import build_slack_blocks, build_test_blocks

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("alert-notify-slack")

SLACK_BOT_TOKEN: str | None = None
SLACK_CHANNEL_ID: str | None = None
VST_ENDPOINT: str | None = None
_slack_client: WebClient | None = None
_http_client: httpx.AsyncClient | None = None
_start_time: float = 0.0
_notification_count: int = 0
_last_error: str | None = None


def _validate_env() -> tuple[str, str, str]:
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    channel = os.environ.get("SLACK_CHANNEL_ID", "").strip()
    vst = os.environ.get("VST_ENDPOINT", "").strip()

    errors: list[str] = []
    if not token:
        errors.append("SLACK_BOT_TOKEN is not set")
    if not channel:
        errors.append("SLACK_CHANNEL_ID is not set")
    if not vst:
        errors.append("VST_ENDPOINT is not set")
    if errors:
        for e in errors:
            logger.error(e)
        logger.error(
            "Set the required environment variables and restart. "
            "See .env.example for reference."
        )
        sys.exit(1)
    return token, channel, vst


def _init_slack_client(token: str) -> WebClient:
    try:
        client = WebClient(token=token)
        response = client.auth_test()
        logger.info("Slack auth OK - bot: %s, team: %s", response["user"], response["team"])
        return client
    except SlackApiError as exc:
        logger.error("Slack auth failed: %s", exc.response["error"])
        sys.exit(1)
    except Exception as exc:
        logger.error("Failed to initialize Slack client: %s", exc)
        sys.exit(1)


def _safe_nested(data, *keys):
    """Safely traverse nested dicts/lists. Returns None on any missing key."""
    current = data
    for k in keys:
        try:
            current = current[k]
        except (KeyError, IndexError, TypeError):
            return None
    return current


# ---------------------------------------------------------------------------
# VST stream resolution
# ---------------------------------------------------------------------------

_sensor_cache: dict[str, str] = {}


def _pick_stream_id(streams: list[dict], fallback: str) -> str:
    """Pick the main streamId from a VST streams response, or fallback."""
    for s in streams:
        if s.get("isMain"):
            return s.get("streamId", fallback)
    return streams[0].get("streamId", fallback) if streams else fallback


async def _resolve_stream_id(sensor_ref: str) -> str | None:
    """Resolve a sensor name or sensorId to a real VST streamId (UUID).

    Strategy:
    1. If sensor_ref is already a UUID sensorId, /sensor/{id}/streams returns 200.
    2. Otherwise, /sensor/list to match by sensorId or name -> /sensor/{id}/streams.
    Results are cached in-process.
    """
    if sensor_ref in _sensor_cache:
        return _sensor_cache[sensor_ref]

    if not _http_client:
        return None

    base = f"http://{VST_ENDPOINT}/vst/api/v1"

    try:
        resp = await _http_client.get(f"{base}/sensor/{sensor_ref}/streams")
        if resp.status_code == 200:
            streams = resp.json()
            if isinstance(streams, list) and streams:
                sid = _pick_stream_id(streams, sensor_ref)
                _sensor_cache[sensor_ref] = sid
                return sid
    except Exception:
        pass

    try:
        resp = await _http_client.get(f"{base}/sensor/list")
        resp.raise_for_status()
        real_sensor_id = None
        for sensor in resp.json():
            if sensor.get("sensorId") == sensor_ref or sensor.get("name") == sensor_ref:
                real_sensor_id = sensor["sensorId"]
                break
        if not real_sensor_id:
            logger.warning("Sensor '%s' not found in VST sensor list", sensor_ref)
            return None
    except Exception as exc:
        logger.warning("Failed to fetch sensor list from VST: %s", exc)
        return None

    try:
        resp = await _http_client.get(f"{base}/sensor/{real_sensor_id}/streams")
        resp.raise_for_status()
        streams = resp.json()
        if isinstance(streams, list) and streams:
            sid = _pick_stream_id(streams, real_sensor_id)
            _sensor_cache[sensor_ref] = sid
            return sid
    except Exception as exc:
        logger.warning("Failed to get streams for sensor %s: %s", real_sensor_id, exc)

    _sensor_cache[sensor_ref] = real_sensor_id
    return real_sensor_id


async def _resolve_video_url(
    stream_id: str,
    start_time: str,
    end_time: str,
) -> str | None:
    """Fetch a temporary video clip URL from VST.

    stream_id may be a sensor name - it will be resolved to a real UUID first.
    Timestamps are passed through to VST verbatim.
    """
    if not VST_ENDPOINT or not _http_client:
        return None

    resolved_id = await _resolve_stream_id(stream_id)
    if not resolved_id:
        return None

    logger.info(
        "Resolving video URL: input=%s, resolved_stream=%s, startTime=%s, endTime=%s",
        stream_id, resolved_id, start_time, end_time,
    )

    url = (
        f"http://{VST_ENDPOINT}/vst/api/v1/storage/file/{resolved_id}/url"
        f"?startTime={start_time}&endTime={end_time}"
        f"&container=mp4&disableAudio=true&expiryMinutes=10080"
    )
    try:
        resp = await _http_client.get(url)
        resp.raise_for_status()
        video_url = resp.json().get("videoUrl")
        if video_url:
            logger.info("Resolved video URL from VST for stream %s", resolved_id)
            return video_url
    except Exception as exc:
        logger.warning("Failed to resolve video URL from VST (stream=%s): %s", resolved_id, exc)
    return None


# ---------------------------------------------------------------------------
# Slack send helper (non-blocking)
# ---------------------------------------------------------------------------

async def _send_slack_message(
    blocks: list[dict], fallback_text: str, color: str,
) -> dict:
    """Post a Slack message without blocking the event loop."""
    loop = asyncio.get_running_loop()
    call = functools.partial(
        _slack_client.chat_postMessage,
        channel=SLACK_CHANNEL_ID,
        text=fallback_text,
        attachments=[{"color": color, "blocks": blocks}],
    )
    return await loop.run_in_executor(None, call)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, VST_ENDPOINT
    global _slack_client, _http_client, _start_time

    logger.info("=" * 60)
    logger.info("Alert Notify Slack - starting up")
    logger.info("=" * 60)

    SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, VST_ENDPOINT = _validate_env()
    logger.info("SLACK_CHANNEL_ID = %s", SLACK_CHANNEL_ID)

    _slack_client = _init_slack_client(SLACK_BOT_TOKEN)

    logger.info("VST_ENDPOINT = %s (resolved by agent via vios-api)", VST_ENDPOINT)
    _http_client = httpx.AsyncClient(timeout=10)

    _start_time = time.time()
    logger.info("Webhook server ready")

    yield

    if _http_client:
        await _http_client.aclose()
    logger.info("Shutting down")


app = FastAPI(
    title="Alert Notify Slack",
    description="Receives VSS incident webhooks and posts rich Slack notifications",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/webhook/alert-notify-slack/health")
async def health():
    """Health check - returns OK if the server is running and Slack is connected."""
    uptime = time.time() - _start_time if _start_time else 0
    return {
        "status": "healthy",
        "uptime_seconds": round(uptime, 1),
        "slack_connected": _slack_client is not None,
        "channel_id": SLACK_CHANNEL_ID,
        "vst_endpoint": VST_ENDPOINT,
        "notifications_sent": _notification_count,
        "last_error": _last_error,
    }


@app.get("/webhook/alert-notify-slack/status")
async def status():
    """Detailed status of the webhook service."""
    uptime = time.time() - _start_time if _start_time else 0
    return {
        "service": "alert-notify-slack",
        "status": "running",
        "uptime_seconds": round(uptime, 1),
        "started_at": datetime.fromtimestamp(_start_time, tz=timezone.utc).isoformat() if _start_time else None,
        "slack": {
            "connected": _slack_client is not None,
            "channel_id": SLACK_CHANNEL_ID,
        },
        "vst": {
            "endpoint": VST_ENDPOINT,
        },
        "stats": {
            "notifications_sent": _notification_count,
            "last_error": _last_error,
        },
    }


@app.post("/webhook/alert-notify-slack")
async def receive_incident(request: Request):
    """Receive an incident payload and send a rich Slack notification."""
    global _notification_count, _last_error

    if not _slack_client:
        raise HTTPException(status_code=503, detail="Slack client not initialized")

    try:
        incident: dict = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info(
        "Received incident: id=%s, category=%s, verdict=%s",
        incident.get("Id", "?"),
        incident.get("category", "?"),
        incident.get("info", {}).get("verdict", "?"),
    )

    info = incident.get("info") or {}
    if not info.get("videoSource"):
        stream_id = (
            info.get("streamId")
            or _safe_nested(incident, "llm", "queries", 0, "params", "streamId")
            or incident.get("sensorId")
        )
        if stream_id:
            resolved_url = await _resolve_video_url(
                stream_id=stream_id,
                start_time=incident.get("timestamp", ""),
                end_time=incident.get("end", incident.get("timestamp", "")),
            )
            if resolved_url:
                incident.setdefault("info", {})["videoSource"] = resolved_url

    try:
        blocks, fallback_text, color = build_slack_blocks(incident)
    except Exception as exc:
        _last_error = f"Format error: {exc}"
        logger.exception("Failed to build Slack message")
        raise HTTPException(status_code=500, detail="Failed to format incident message")

    try:
        response = await _send_slack_message(blocks, fallback_text, color)
        _notification_count += 1
        _last_error = None
        logger.info(
            "Slack notification sent: channel=%s, ts=%s",
            SLACK_CHANNEL_ID,
            response.get("ts", "?"),
        )
        return {
            "status": "sent",
            "slack_ts": response.get("ts"),
            "channel": SLACK_CHANNEL_ID,
        }

    except SlackApiError as exc:
        _last_error = f"Slack API: {exc.response['error']}"
        logger.error("Slack API error: %s", exc.response["error"])
        raise HTTPException(
            status_code=502,
            detail=f"Slack API error: {exc.response['error']}",
        )
    except Exception as exc:
        _last_error = f"Send error: {exc}"
        logger.exception("Failed to send Slack notification")
        raise HTTPException(status_code=500, detail="Failed to send Slack notification")


@app.post("/webhook/alert-notify-slack/test")
async def send_test():
    """Send a test notification to verify Slack integration."""
    global _notification_count, _last_error

    if not _slack_client:
        raise HTTPException(status_code=503, detail="Slack client not initialized")

    try:
        blocks, fallback_text, color = build_test_blocks()
    except Exception as exc:
        _last_error = f"Test format error: {exc}"
        raise HTTPException(status_code=500, detail="Failed to build test message")

    try:
        response = await _send_slack_message(blocks, fallback_text, color)
        _notification_count += 1
        _last_error = None
        logger.info("Test notification sent: ts=%s", response.get("ts", "?"))
        return {
            "status": "sent",
            "message": "Test notification delivered to Slack",
            "slack_ts": response.get("ts"),
            "channel": SLACK_CHANNEL_ID,
        }

    except SlackApiError as exc:
        _last_error = f"Slack API: {exc.response['error']}"
        raise HTTPException(
            status_code=502,
            detail=f"Slack API error: {exc.response['error']}",
        )
    except Exception as exc:
        _last_error = f"Send error: {exc}"
        logger.exception("Failed to send test notification")
        raise HTTPException(status_code=500, detail="Failed to send test notification")


@app.post("/webhook/alert-notify-slack/stop")
async def stop_server():
    """Gracefully stop the webhook server."""
    logger.info("Stop requested via API - shutting down")

    async def _shutdown():
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_shutdown())

    return {
        "status": "stopping",
        "message": "Server is shutting down",
        "notifications_sent": _notification_count,
    }


def main():
    import uvicorn

    host = os.environ.get("WEBHOOK_HOST", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_PORT", "9090"))

    logger.info("Starting on %s:%d", host, port)
    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
