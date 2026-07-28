---
name: configure-webhook-bdd-test
description: Add or update the local Docker Compose webhook receivers required by the VIOS webhook notification BDD test. Use when preparing deployment/stream-processing/docker-compose/configs/notification_config.json for test_webhook_notifications.py, or when that test times out waiting for camera_add, camera_streaming, or camera_remove.
---

# Configure Webhook BDD Test

The suite covers two sensor types. File-sensor scenarios always run; the RTSP
scenario is skipped unless `tests.notification_tests.test_parameters.rtsp_sensor`
is set in `test/bdd_tests/config.json`. Configure **both** sets of receivers —
a config with only the `["file"]` receivers silently starves every RTSP scenario,
because a receiver filtered to `file` never sees an RTSP camera event.

1. Edit only `deployment/stream-processing/docker-compose/configs/notification_config.json`. Preserve unrelated settings and receivers.
2. Set `webhooks.enabled` to `true` and give each event group the `id` and `enabled` values below. The `id` is copied into the delivered body as `webhook_id`, and `test/bdd_tests/config.json` asserts these exact values under `webhook_ids`:

| Event group | `id` | `enabled` |
|---|---|---|
| `camera_add` | `wh-001` | `true` |
| `camera_streaming` | `wh-002` | `true` |
| `camera_remove` | `wh-003` | `true` |

3. Under each matching `camera_status_change`, ensure these BDD requests exist exactly once. The URL suffix matches the key under `webhook_paths` in `test/bdd_tests/config.json` — keep the two in sync when adding a receiver:

**File-sensor receivers** (used by the file upload scenarios):

| Event | Method | URL | `camera_type` |
|---|---|---|---|
| `camera_add` | `POST` | `http://127.0.0.1:18088/bdd/webhooks/camera/add` | `["file"]` |
| `camera_streaming` | `PUT` | `http://127.0.0.1:18088/bdd/webhooks/camera/streaming` | `["file"]` |
| `camera_remove` | `DELETE` | `http://127.0.0.1:18088/bdd/webhooks/camera/remove` | `["file"]` |

**RTSP-sensor receivers** (used by the RTSP scenario, and as the negative filter case):

| Event | Method | URL | `camera_type` |
|---|---|---|---|
| `camera_add` | `POST` | `http://127.0.0.1:18088/bdd/webhooks/camera/add/rtsp-only` | `["rtsp"]` |
| `camera_streaming` | `PUT` | `http://127.0.0.1:18088/bdd/webhooks/camera/streaming/rtsp-only` | `["rtsp"]` |
| `camera_remove` | `DELETE` | `http://127.0.0.1:18088/bdd/webhooks/camera/remove/rtsp-only` | `["rtsp"]` |

**Unfiltered receiver** (proves an omitted `camera_type` accepts every type):

| Event | Method | URL | `camera_type` |
|---|---|---|---|
| `camera_add` | `POST` | `http://127.0.0.1:18088/bdd/webhooks/camera/add/unfiltered` | Omit the field |

`camera_add/rtsp-only` does double duty: the file scenarios assert it receives
*nothing* for a file sensor, and an RTSP sensor delivers to it. Keep it filtered
to `["rtsp"]` exactly — widening it to `["rtsp", "file"]` breaks the negative test.

4. Give every BDD request these remaining fields:

```json
{
  "headers": {"Content-Type": "application/json", "streamId": "{{event.camera_id}}"},
  "query_params": {"change": "{{event.change}}"},
  "timeout_ms": 5000,
  "retry": {"max_attempts": 3, "backoff_ms": [1000, 5000, 15000], "retry_on_status": [408, 429, 500, 502, 503, 504]}
}
```

The header name is `streamId` — the tests read it case-insensitively but do not
accept `x-stream-id`. Omit `auth` on BDD requests; the in-process receiver does
not verify signatures and an unresolved `{{secrets.*}}` placeholder only adds a
failure path.

5. Set or omit `camera_type` exactly as shown. If an event group is absent, create an enabled group with its `id`, `camera_status_change`, and `request` array. Update an existing BDD request in place instead of adding a duplicate.
6. Validate the edited file:

```bash
python3 -m json.tool deployment/stream-processing/docker-compose/configs/notification_config.json
```

7. Confirm the deployed image actually supports webhooks. Images built before the
   webhook feature ignore this config entirely, and the only symptom is every
   scenario timing out with zero captured requests:

```bash
docker exec streamprocessing-ms-1 grep -ac "notification_config.json" /home/vst/vst_release/launch_vst
```

`0` means the running image predates the feature regardless of its tag — rebuild
(`./build.sh container module=sensor,streamprocessing tag=<tag>`) and redeploy
before running the tests. `1` or more means webhook support is present.

8. Tell the user to restart VST if it was already running, because VST loads this config at startup.
9. Do not run pytest unless requested. Finish by giving both commands, to be run from `test/bdd_tests`:

```bash
poetry run pytest tests/notification/test_webhook_notifications.py
poetry run pytest tests/notification/test_webhook_notifications.py -v --tb=short --log-cli-level=INFO
```
