---
name: configure-webhook-bdd-test
description: Add or update the local Docker Compose webhook receivers required by the VIOS webhook notification BDD test. Use when preparing deployment/stream-processing/docker-compose/configs/notification_config.json for test_webhook_notifications.py, or when that test times out waiting for camera_add, camera_streaming, or camera_remove.
---

# Configure Webhook BDD Test

1. Edit only `deployment/stream-processing/docker-compose/configs/notification_config.json`. Preserve unrelated settings and receivers.
2. Under each matching `camera_status_change`, ensure these BDD requests exist exactly once:

| Event | Method | URL | `camera_type` |
|---|---|---|---|
| `camera_add` | `POST` | `http://127.0.0.1:18088/bdd/webhooks/camera/add` | `["file"]` |
| `camera_add` | `POST` | `http://127.0.0.1:18088/bdd/webhooks/camera/add/unfiltered` | Omit the field |
| `camera_add` | `POST` | `http://127.0.0.1:18088/bdd/webhooks/camera/add/rtsp-only` | `["rtsp"]` |
| `camera_streaming` | `PUT` | `http://127.0.0.1:18088/bdd/webhooks/camera/streaming` | `["file"]` |
| `camera_streaming` | `PUT` | `http://127.0.0.1:18088/bdd/webhooks/camera/streaming/rtsp-only` | `["rtsp"]` |
| `camera_remove` | `DELETE` | `http://127.0.0.1:18088/bdd/webhooks/camera/remove` | `["file"]` |

The `streaming/rtsp-only` receiver serves the RTSP `camera_streaming` scenario, which is skipped unless `tests.notification_tests.test_parameters.rtsp_sensor` is set in `test/bdd_tests/config.json`.

3. Give every BDD request these remaining fields:

```json
{
  "headers": {"Content-Type": "application/json", "streamId": "{{event.camera_id}}"},
  "query_params": {"change": "{{event.change}}"},
  "timeout_ms": 5000,
  "retry": {"max_attempts": 3, "backoff_ms": [1000, 5000, 15000], "retry_on_status": [408, 429, 500, 502, 503, 504]}
}
```

4. Set or omit `camera_type` exactly as shown in the table. If an event group is absent, create an enabled group with its `camera_status_change` and `request` array. Update an existing BDD request in place instead of adding a duplicate.
5. Validate the edited file with `python3 -m json.tool deployment/stream-processing/docker-compose/configs/notification_config.json`. Tell the user to restart VST if it was already running because VST loads this config at startup.
6. Do not run pytest unless requested. Finish by giving both commands, to be run from `test/bdd_tests`:

```bash
poetry run pytest tests/notification/test_webhook_notifications.py
poetry run pytest tests/notification/test_webhook_notifications.py -v --tb=short --log-cli-level=INFO
```
